"""
Formulation signature helpers.

We use signatures to detect "baseline repeats" (a candidate formulation that is
effectively identical to a previously tested formulation under similar target
conditions).

Design goals:
- Stable across whitespace/case/unicode (e.g., H₂O vs H2O).
- Order-invariant (mixtures have no intrinsic ordering).
- Deterministic and cheap to compute (no LLM calls).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, List, Tuple


_SUBSCRIPT_DIGITS = str.maketrans(
    {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
    }
)


# Minimal aliasing for common DES component names/abbreviations.
# This is intentionally small and conservative: it only handles high-frequency
# short-hands seen in practice to improve baseline matching robustness.
_NAME_ALIASES: dict[str, str] = {
    "cholinechloride": "chcl",
    "ethyleneglycol": "eg",
    "water": "h2o",
    "h2o": "h2o",
    "chcl": "chcl",
    "eg": "eg",
}


def _as_clean_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def _normalize_ratio_part(x: Any) -> str:
    s = _as_clean_str(x)
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).translate(_SUBSCRIPT_DIGITS).strip()
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return s

    d_norm = d.normalize()
    if d_norm == d_norm.to_integral():
        return str(d_norm.to_integral())

    out = format(d_norm, "f").rstrip("0").rstrip(".")
    return out or "0"


def normalize_component_name(name: Any) -> str:
    """
    Normalize a component name for signature matching.

    Rules:
    - Unicode normalize + translate subscripts.
    - Prefer short alphanumeric abbreviations in parentheses if present.
    - Lowercase and remove non-alphanumeric chars.
    - Apply a small alias map for common names.
    """
    raw = _as_clean_str(name)
    if not raw:
        return ""

    s = unicodedata.normalize("NFKC", raw).translate(_SUBSCRIPT_DIGITS)

    # Extract candidate abbreviations from parentheses (e.g., "Choline chloride (ChCl)").
    abbrs: list[str] = []
    for inner in re.findall(r"\(([^)]{1,24})\)", s):
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", inner).strip().lower()
        if 2 <= len(cleaned) <= 10:
            abbrs.append(cleaned)
    abbr = min(abbrs, key=len) if abbrs else ""

    base = re.sub(r"[^A-Za-z0-9]+", "", s).strip().lower()
    if abbr:
        base = abbr

    return _NAME_ALIASES.get(base, base)


def _ratio_parts(molar_ratio: Any) -> list[str]:
    s = _as_clean_str(molar_ratio)
    if not s:
        return []
    s = unicodedata.normalize("NFKC", s).translate(_SUBSCRIPT_DIGITS)
    parts = [p.strip() for p in s.split(":")]
    return [_normalize_ratio_part(p) for p in parts if p.strip()]


def compute_formulation_signature(formulation: Any, expected_num_components: int) -> str:
    """
    Compute a canonical, order-invariant signature for a formulation dict.

    Supported shapes (matching our UI expectations):
    - Binary: {"HBD": str, "HBA": str, "molar_ratio": "a:b"}
    - Multi: {"components": [{"name": ...}, ...], "molar_ratio": "a:b:c", "num_components": N}
    """
    if not isinstance(formulation, dict):
        return ""

    if expected_num_components == 2:
        hbd = normalize_component_name(formulation.get("HBD"))
        hba = normalize_component_name(formulation.get("HBA"))
        parts = _ratio_parts(formulation.get("molar_ratio"))
        ratio_hbd = parts[0] if len(parts) >= 1 else ""
        ratio_hba = parts[1] if len(parts) >= 2 else ""

        pairs = [(hbd, ratio_hbd), (hba, ratio_hba)]
        pairs_sorted = sorted([p for p in pairs if p[0]], key=lambda t: t[0])
        body = "|".join([f"{n}={r}" for n, r in pairs_sorted])
        return f"sig_v1|n={expected_num_components}|{body}"

    comps = formulation.get("components")
    if not isinstance(comps, list):
        return ""

    names: list[str] = []
    for c in comps:
        if isinstance(c, dict):
            names.append(normalize_component_name(c.get("name")))
        else:
            names.append(normalize_component_name(c))

    parts = _ratio_parts(formulation.get("molar_ratio"))
    while len(parts) < len(names):
        parts.append("")
    pairs = list(zip(names, parts[: len(names)]))

    # Handle duplicates by appending a stable counter.
    seen: dict[str, int] = {}
    pairs_norm: list[Tuple[str, str]] = []
    for n, r in pairs:
        if not n:
            continue
        k = n
        if k in seen:
            seen[k] += 1
            k = f"{k}#{seen[n]}"
        else:
            seen[k] = 1
        pairs_norm.append((k, r))

    pairs_sorted = sorted(pairs_norm, key=lambda t: t[0])
    body = "|".join([f"{n}={r}" for n, r in pairs_sorted])
    return f"sig_v1|n={expected_num_components}|{body}"


@dataclass(frozen=True)
class BaselineRecord:
    baseline_id: str
    signature: str
    target_material_norm: str
    target_temperature_C: float | None
    max_efficiency_value: float | None
    max_efficiency_unit: str
    formulation_summary: str = ""


def normalize_material_name(x: Any) -> str:
    s = _as_clean_str(x).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_temperature_C(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def temperature_match(a: float | None, b: float | None, *, tol_C: float) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= float(tol_C)
