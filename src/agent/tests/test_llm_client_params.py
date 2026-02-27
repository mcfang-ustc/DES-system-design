"""
Unit tests for LLMClient request parameter compatibility.

Focus: OpenAI GPT-5.* reasoning mode disallows sampling params such as temperature
when reasoning_effort is enabled. We enforce that in LLMClient so production
doesn't fail with runtime 400s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class _DummyMessage:
    content: str


@dataclass
class _DummyChoice:
    message: _DummyMessage


@dataclass
class _DummyResponse:
    choices: list[_DummyChoice]


class _DummyChatCompletions:
    def __init__(self):
        self.last_params: Optional[Dict[str, Any]] = None

    def create(self, **params):  # noqa: ANN003 - matches OpenAI client's API
        self.last_params = params
        return _DummyResponse(choices=[_DummyChoice(message=_DummyMessage(content="ok"))])


class _DummyChat:
    def __init__(self):
        self.completions = _DummyChatCompletions()


class _DummyOpenAI:
    def __init__(self):
        self.chat = _DummyChat()


def test_openai_reasoning_effort_drops_temperature():
    from agent.utils.llm_client import LLMClient

    llm = LLMClient(
        provider="openai",
        model="gpt-5.2",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        temperature=0.1,
        max_tokens=1234,
        reasoning_effort="medium",
    )

    # Replace network client with dummy
    dummy = _DummyOpenAI()
    llm.client = dummy  # type: ignore[assignment]

    out = llm.chat("hello", temperature=0.9)  # override should be ignored
    assert out == "ok"

    params = dummy.chat.completions.last_params
    assert params is not None
    assert params["model"] == "gpt-5.2"
    assert params["reasoning_effort"] == "medium"

    # OpenAI uses max_completion_tokens, and must NOT send temperature
    assert params.get("max_completion_tokens") == 1234
    assert "temperature" not in params


def test_openai_reasoning_none_keeps_temperature():
    from agent.utils.llm_client import LLMClient

    llm = LLMClient(
        provider="openai",
        model="gpt-5.2",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        temperature=0.2,
        max_tokens=200,
        reasoning_effort="none",
    )
    dummy = _DummyOpenAI()
    llm.client = dummy  # type: ignore[assignment]

    _ = llm.chat("hi")
    params = dummy.chat.completions.last_params
    assert params is not None
    assert params["reasoning_effort"] == "none"
    assert params["temperature"] == pytest.approx(0.2)
    assert params["max_completion_tokens"] == 200


def test_dashscope_uses_max_tokens_and_temperature():
    from agent.utils.llm_client import LLMClient

    llm = LLMClient(
        provider="dashscope",
        model="qwen-plus",
        api_key="sk-test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=0.3,
        max_tokens=321,
    )
    dummy = _DummyOpenAI()
    llm.client = dummy  # type: ignore[assignment]

    _ = llm.chat("hi")
    params = dummy.chat.completions.last_params
    assert params is not None
    assert params["max_tokens"] == 321
    assert params["temperature"] == pytest.approx(0.3)
