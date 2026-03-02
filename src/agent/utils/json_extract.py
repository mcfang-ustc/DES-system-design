"""
JSON extraction helpers.

Motivation:
- LLMs do not always wrap JSON in ```json fences, even when instructed.
- Using a single regex is brittle (especially when the output contains braces in prose).
- For reliability, we try multiple parsing strategies, including a simple
  bracket-matching extractor for the first top-level JSON object.
"""

from __future__ import annotations

from typing import Any, Optional
import json
import re


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def _extract_fenced_payload(text: str) -> Optional[str]:
    if not text:
        return None
    m = _FENCED_JSON_RE.search(text)
    if not m:
        return None
    payload = (m.group(1) or "").strip()
    return payload or None


def extract_first_json_object(text: str) -> Optional[str]:
    """
    Extract the first top-level JSON object substring from `text`.

    This is a lightweight bracket matcher that is robust to braces inside strings.
    It only extracts objects starting with '{' and ending at the matching '}'.
    """
    if not text:
        return None

    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        # not in_string
        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def loads_json_from_text(text: str) -> Optional[Any]:
    """
    Best-effort JSON loader from free-form text.

    Strategy (in order):
    1) ```json fenced block (or generic fenced block)
    2) direct json.loads(text)
    3) extract first top-level JSON object with bracket matching, then json.loads()
    """
    if text is None:
        return None

    t = str(text).strip()
    if not t:
        return None

    # 1) fenced block
    fenced = _extract_fenced_payload(t)
    if fenced:
        try:
            return json.loads(fenced)
        except Exception:
            pass

    # 2) direct JSON
    if t[0] in "{[":
        try:
            return json.loads(t)
        except Exception:
            pass

    # 3) bracket-match first object
    obj = extract_first_json_object(t)
    if obj:
        try:
            return json.loads(obj)
        except Exception:
            pass

    return None

