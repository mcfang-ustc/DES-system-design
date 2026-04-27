"""
Serialization helpers for persisting agent state.

Why this exists:
- We persist Recommendations / Trajectories / tool results as JSON.
- Some tool stacks (notably CoreRAG via owlready2) can return rich Python
  objects (e.g., LogicalClassConstruct) that are not deepcopy()-safe and not
  JSON-serializable.
- Python's dataclasses.asdict() uses copy.deepcopy() internally, which can
  crash on such objects at save-time.

Policy:
- Keep as much structure as possible for dict/list-like objects.
- For unknown / non-serializable objects, DO NOT drop them; convert to str(obj)
  so we preserve information for later inspection.
"""

from __future__ import annotations

from dataclasses import is_dataclass, fields as dc_fields
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Set


_PRIMITIVE_TYPES = (str, int, float, bool, type(None))


def to_jsonable(obj: Any, *, _seen: Set[int] | None = None, _depth: int = 0) -> Any:
    """
    Convert arbitrary Python objects into a JSON-serializable structure.

    - dict/list/tuple/set are converted recursively.
    - Pydantic models are converted via model_dump()/dict().
    - dataclasses are converted field-by-field without using dataclasses.asdict
      (which would deepcopy and may crash).
    - Path/datetime/date are stringified to stable representations.
    - Unknown objects are converted to str(obj) (never dropped).
    - Cycles are broken by returning str(obj).
    """
    if isinstance(obj, _PRIMITIVE_TYPES):
        return obj

    if _seen is None:
        _seen = set()

    obj_id = id(obj)
    if obj_id in _seen:
        # Break cycles without crashing; keep information.
        try:
            return str(obj)
        except Exception:
            return f"<circular_ref depth={_depth}>"

    _seen.add(obj_id)

    # Common "safe" rich types
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)

    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return to_jsonable(obj.model_dump(), _seen=_seen, _depth=_depth + 1)
        except Exception:
            pass

    # Pydantic v1 fallback
    if hasattr(obj, "dict"):
        try:
            return to_jsonable(obj.dict(), _seen=_seen, _depth=_depth + 1)  # type: ignore[misc]
        except Exception:
            pass

    # dataclasses (without dataclasses.asdict)
    if is_dataclass(obj):
        try:
            data: Dict[str, Any] = {}
            for f in dc_fields(obj):
                data[f.name] = to_jsonable(
                    getattr(obj, f.name), _seen=_seen, _depth=_depth + 1
                )
            return data
        except Exception:
            # Fall through to stringification
            pass

    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            # JSON keys must be strings
            try:
                key = k if isinstance(k, str) else str(k)
            except Exception:
                key = "<unstringifiable_key>"
            out[key] = to_jsonable(v, _seen=_seen, _depth=_depth + 1)
        return out

    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v, _seen=_seen, _depth=_depth + 1) for v in obj]

    # Last resort: preserve information as string (do not drop)
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return f"<unserializable type={type(obj).__name__}>"

