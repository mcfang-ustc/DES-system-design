"""
Formulation validation & summarization helpers.

The web UI expects either:
- Binary DES: {"HBD": str, "HBA": str, "molar_ratio": str}
- Multi-component DES: {"components": [...], "molar_ratio": str, "num_components": int}

This module enforces "num_components is fixed" tasks (e.g. 5 components must be 5),
so invalid/empty formulations never get persisted as PENDING recommendations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _as_clean_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def _is_nonempty_nonunknown(x: Any) -> bool:
    s = _as_clean_str(x)
    if not s:
        return False
    return s.lower() != "unknown"


def summarize_formulation(formulation: Any) -> str:
    """Create a short, human-readable one-liner for logs/trajectory."""
    if not isinstance(formulation, dict):
        return "<invalid formulation>"

    comps = formulation.get("components")
    if isinstance(comps, list) and comps:
        names: List[str] = []
        for c in comps:
            if isinstance(c, dict):
                n = _as_clean_str(c.get("name"))
            else:
                n = _as_clean_str(c)
            if n:
                names.append(n)
        ratio = _as_clean_str(formulation.get("molar_ratio"))
        joined = " + ".join(names) if names else "<components>"
        return f"{joined} ({ratio})" if ratio else joined

    hbd = _as_clean_str(formulation.get("HBD"))
    hba = _as_clean_str(formulation.get("HBA"))
    ratio = _as_clean_str(formulation.get("molar_ratio"))
    if hbd and hba:
        base = f"{hbd} : {hba}"
        return f"{base} ({ratio})" if ratio else base

    return "<invalid formulation>"


def normalize_formulation(formulation: Any, expected_num_components: int) -> Dict[str, Any]:
    """
    Normalize common minor schema differences without inventing chemistry.

    Examples:
    - missing num_components -> fill from expected_num_components
    - non-string molar_ratio -> stringify
    - component fields -> stringify
    """
    if not isinstance(formulation, dict):
        return {}

    f: Dict[str, Any] = dict(formulation)

    # Normalize molar_ratio to str if present
    if "molar_ratio" in f:
        f["molar_ratio"] = _as_clean_str(f.get("molar_ratio"))

    if expected_num_components != 2:
        comps = f.get("components")
        if isinstance(comps, list):
            norm_comps: List[Dict[str, Any]] = []
            for c in comps:
                if isinstance(c, dict):
                    norm_comps.append(
                        {
                            "name": _as_clean_str(c.get("name")),
                            "role": _as_clean_str(c.get("role")),
                            "function": _as_clean_str(c.get("function")),
                        }
                    )
                else:
                    # Keep structure, but stringify unknown objects
                    norm_comps.append(
                        {"name": _as_clean_str(c), "role": "", "function": ""}
                    )
            f["components"] = norm_comps

        # Fill num_components if absent (we still validate length separately).
        if f.get("num_components") is None:
            f["num_components"] = expected_num_components

    return f


def validate_formulation(
    formulation: Any, expected_num_components: int, *, require_functions: bool = True
) -> Tuple[bool, List[str]]:
    """
    Validate formulation structure for fixed-num-components tasks.

    Returns:
        (ok, errors)
    """
    errors: List[str] = []

    if not isinstance(expected_num_components, int) or expected_num_components < 2:
        errors.append(f"expected_num_components must be >=2 int, got {expected_num_components!r}")
        return False, errors

    if not isinstance(formulation, dict):
        errors.append("formulation is not an object/dict")
        return False, errors

    if expected_num_components == 2:
        if not _is_nonempty_nonunknown(formulation.get("HBD")):
            errors.append("missing/empty HBD")
        if not _is_nonempty_nonunknown(formulation.get("HBA")):
            errors.append("missing/empty HBA")
        if not _is_nonempty_nonunknown(formulation.get("molar_ratio")):
            errors.append("missing/empty molar_ratio")
        return (len(errors) == 0), errors

    # Multi-component
    comps = formulation.get("components")
    if not isinstance(comps, list):
        errors.append("components must be a list")
        return False, errors

    if len(comps) != expected_num_components:
        errors.append(f"components length must be {expected_num_components}, got {len(comps)}")

    for idx, c in enumerate(comps):
        if not isinstance(c, dict):
            errors.append(f"component[{idx}] is not an object")
            continue
        if not _is_nonempty_nonunknown(c.get("name")):
            errors.append(f"component[{idx}].name missing/empty")
        if not _is_nonempty_nonunknown(c.get("role")):
            errors.append(f"component[{idx}].role missing/empty")
        if require_functions and not _is_nonempty_nonunknown(c.get("function")):
            errors.append(f"component[{idx}].function missing/empty")

    ratio = formulation.get("molar_ratio")
    if not _is_nonempty_nonunknown(ratio):
        errors.append("missing/empty molar_ratio")
    else:
        # Optional structure check: for N components, ratio should have N-1 colons.
        ratio_s = _as_clean_str(ratio)
        if ratio_s.count(":") != (expected_num_components - 1):
            errors.append(
                f"molar_ratio should have {expected_num_components - 1} ':' separators for {expected_num_components} components"
            )

    # num_components field is helpful but not strictly required if components length matches.
    nc = formulation.get("num_components")
    if nc is not None:
        try:
            nc_int = int(nc)
            if nc_int != expected_num_components:
                errors.append(f"num_components must be {expected_num_components}, got {nc_int}")
        except Exception:
            errors.append("num_components is not an integer")

    return (len(errors) == 0), errors

