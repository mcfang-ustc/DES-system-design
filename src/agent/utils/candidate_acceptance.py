"""
Candidate acceptance & classification.

This module is intentionally deterministic (no LLM calls).

It supports:
- Classifying a candidate as RECOMMENDATION vs BASELINE (repeat of a tested system).
- Enforcing "delta requirement" when a relevant baseline exists.
- Deciding whether a candidate is ACCEPTED (can trigger early stop / be selected as final).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from .formulation_signature import (
    BaselineRecord,
    compute_formulation_signature,
    normalize_material_name,
    normalize_temperature_C,
    temperature_match,
)


RecommendationClass = Literal["RECOMMENDATION", "BASELINE"]


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    recommendation_class: RecommendationClass
    baseline_reference: str
    delta_to_baseline: List[Dict[str, str]]
    reasons: List[str]
    candidate_signature: str
    matched_baseline_id: Optional[str]


def _as_delta_list(x: Any) -> List[Dict[str, str]]:
    """
    Normalize delta_to_baseline into a JSON-safe list[{"change","rationale"}].

    We keep the structure for UI and later analysis, but remain tolerant:
    - list[str] -> list[{"change": str, "rationale": ""}]
    - dict -> [dict] (key-mapped if needed)
    - other -> [{"change": str(x), "rationale": ""}]
    """
    if x is None:
        return []

    if isinstance(x, list):
        items = x
    else:
        items = [x]

    out: List[Dict[str, str]] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, dict):
            change = str(item.get("change") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            # Back-compat for older shapes
            if not change and item.get("delta") is not None:
                change = str(item.get("delta")).strip()
            if not rationale and item.get("reason") is not None:
                rationale = str(item.get("reason")).strip()
            if change or rationale:
                out.append({"change": change, "rationale": rationale})
        else:
            s = str(item).strip()
            if s:
                out.append({"change": s, "rationale": ""})
    return out


def _select_best_baseline_for_reference(
    baselines: List[BaselineRecord],
) -> Optional[BaselineRecord]:
    """
    Choose a baseline for "baseline_reference" when the candidate is NOT a repeat.

    Heuristic: prefer baselines with higher numeric max efficiency (if available),
    otherwise keep deterministic first item.
    """
    if not baselines:
        return None

    with_values = [b for b in baselines if isinstance(b.max_efficiency_value, (int, float))]
    if with_values:
        return max(with_values, key=lambda b: float(b.max_efficiency_value or 0.0))
    return baselines[0]


def _is_percent_unit(unit: str) -> bool:
    u = (unit or "").strip().lower()
    return u in {"%", "percent", "pct", "percentage"}


def evaluate_candidate_acceptance(
    candidate: Dict[str, Any],
    *,
    task: Dict[str, Any],
    expected_num_components: int,
    baselines: List[BaselineRecord],
    schema_valid: bool,
    baseline_min_percent: float,
    temperature_tolerance_C: float,
    require_delta_when_baseline_exists: bool,
) -> AcceptanceResult:
    """
    Determine whether a schema-valid candidate is ACCEPTED.

    Rules:
    1) If the candidate repeats a baseline signature under matching (material,temp),
       then class=BASELINE. Acceptance depends on baseline performance:
         - if percent-like unit and value < baseline_min_percent -> rejected
         - else accepted
    2) If it is NOT a baseline repeat, then class=RECOMMENDATION.
       If a relevant baseline exists for the task, enforce delta_to_baseline non-empty.
    """
    reasons: List[str] = []

    formulation = candidate.get("formulation")
    cand_sig = compute_formulation_signature(formulation, expected_num_components)

    task_material = normalize_material_name(task.get("target_material"))
    task_temp = normalize_temperature_C(task.get("target_temperature"))

    relevant_baselines: List[BaselineRecord] = []
    matching_baselines: List[BaselineRecord] = []
    for b in baselines:
        if b.target_material_norm and task_material and b.target_material_norm != task_material:
            continue
        if task_temp is not None and b.target_temperature_C is not None:
            if not temperature_match(task_temp, b.target_temperature_C, tol_C=temperature_tolerance_C):
                continue
        relevant_baselines.append(b)
        if b.signature and cand_sig and b.signature == cand_sig:
            matching_baselines.append(b)

    # Normalize fields (even if they come from LLM structured outputs).
    baseline_reference = str(candidate.get("baseline_reference") or "").strip() or "none"
    delta_to_baseline = _as_delta_list(candidate.get("delta_to_baseline"))

    if not schema_valid:
        reasons.append("schema_invalid")
        return AcceptanceResult(
            accepted=False,
            recommendation_class="RECOMMENDATION",
            baseline_reference=baseline_reference,
            delta_to_baseline=delta_to_baseline,
            reasons=reasons,
            candidate_signature=cand_sig,
            matched_baseline_id=None,
        )

    if matching_baselines:
        b = _select_best_baseline_for_reference(matching_baselines) or matching_baselines[0]
        baseline_reference = b.baseline_id
        delta_to_baseline = []

        if _is_percent_unit(b.max_efficiency_unit) and b.max_efficiency_value is not None:
            if float(b.max_efficiency_value) < float(baseline_min_percent):
                reasons.append("baseline_underperformed")
                reasons.append(f"baseline_max_efficiency={b.max_efficiency_value}{b.max_efficiency_unit}")

        return AcceptanceResult(
            accepted=(len(reasons) == 0),
            recommendation_class="BASELINE",
            baseline_reference=baseline_reference,
            delta_to_baseline=delta_to_baseline,
            reasons=reasons,
            candidate_signature=cand_sig,
            matched_baseline_id=b.baseline_id,
        )

    # Not a baseline repeat -> RECOMMENDATION
    if relevant_baselines:
        best = _select_best_baseline_for_reference(relevant_baselines)
        if best is not None:
            if baseline_reference.lower() in {"", "none", "n/a", "na", "null"}:
                baseline_reference = best.baseline_id

    if require_delta_when_baseline_exists and relevant_baselines:
        if not delta_to_baseline:
            reasons.append("missing_delta_to_baseline")

    return AcceptanceResult(
        accepted=(len(reasons) == 0),
        recommendation_class="RECOMMENDATION",
        baseline_reference=baseline_reference,
        delta_to_baseline=delta_to_baseline,
        reasons=reasons,
        candidate_signature=cand_sig,
        matched_baseline_id=None,
    )
