"""
LLM Client using OpenAI-compatible API

Supports:
- OpenAI API
- DashScope (Aliyun) OpenAI-compatible endpoint
- Any OpenAI-compatible service
"""

import os
import logging
import re
import threading
from typing import Optional, Dict, Any, Iterable
from openai import OpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Universal LLM client using OpenAI-compatible API.

    Supports multiple providers:
    - OpenAI (api.openai.com)
    - DashScope/Aliyun (dashscope.aliyuncs.com/compatible-mode/v1)
    - Custom OpenAI-compatible endpoints

    Attributes:
        client: OpenAI client instance
        model: Model name
        temperature: Sampling temperature
        max_tokens: Maximum completion tokens
    """

    def __init__(
        self,
        provider: str = "dashscope",
        model: str = "qwen-plus",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        """
        Initialize LLM client.

        Args:
            provider: "openai", "dashscope", or "custom"
            model: Model name (e.g., "gpt-4o-mini", "qwen-plus")
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens in response
            api_key: API key (if None, read from env)
            base_url: Custom base URL (for custom providers)
        """
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # OpenAI-only (gpt-5.* etc.). If set to non-"none", some sampling params
        # (temperature/top_p/logprobs) become invalid and must be omitted.
        self.reasoning_effort = reasoning_effort
        # OpenAI-only verbosity control (low/medium/high). Safe to omit.
        self.verbosity = verbosity

        # Determine API key and base URL
        if provider == "openai":
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            self.base_url = base_url or "https://api.openai.com/v1"

        elif provider == "dashscope":
            self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
            self.base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        else:  # custom
            self.api_key = api_key or os.getenv("LLM_API_KEY")
            if not base_url:
                raise ValueError("base_url is required for custom provider")
            self.base_url = base_url

        if not self.api_key:
            raise ValueError(
                f"API key not found for provider '{provider}'. "
                f"Set {provider.upper()}_API_KEY in environment or .env file."
            )

        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        # Some OpenAI SDK versions (and some OpenAI-compatible vendors) do not
        # accept newer kwargs (e.g., verbosity) in chat.completions.create.
        # We learn unsupported parameters at runtime and avoid passing them
        # again to keep the system resilient in production images.
        self._unsupported_chat_params: set[str] = set()
        self._unsupported_chat_params_lock = threading.Lock()

        logger.info(
            f"Initialized LLM client: provider={provider}, model={model}, "
            f"temperature={temperature}"
        )

    @staticmethod
    def _is_reasoning_enabled(reasoning_effort: Optional[str]) -> bool:
        """Return True when reasoning is enabled (i.e., not None/"none")."""
        if reasoning_effort is None:
            return False
        return str(reasoning_effort).strip().lower() != "none"

    @staticmethod
    def _pop_any(d: Dict[str, Any], keys: Iterable[str]) -> None:
        """Pop keys from dict if present (in-place)."""
        for k in keys:
            d.pop(k, None)

    @staticmethod
    def _parse_unexpected_kwarg(err: TypeError) -> Optional[str]:
        """
        Extract the unexpected kwarg name from typical Python TypeError messages.

        Example:
            "Completions.create() got an unexpected keyword argument 'verbosity'"
        """
        msg = str(err)
        m = re.search(r"unexpected keyword argument '([^']+)'", msg)
        if not m:
            return None
        return m.group(1)

    def _is_chat_param_unsupported(self, name: str) -> bool:
        with self._unsupported_chat_params_lock:
            return name in self._unsupported_chat_params

    def _mark_chat_param_unsupported(self, name: str) -> None:
        with self._unsupported_chat_params_lock:
            self._unsupported_chat_params.add(name)

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        Send a chat completion request.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Override default temperature
            max_tokens: Override default max_tokens
            reasoning_effort: OpenAI reasoning effort (e.g., "none", "low", "medium", "high")
            verbosity: OpenAI verbosity (e.g., "low", "medium", "high")
            **kwargs: Additional parameters for API call

        Returns:
            Generated text response
        """
        # Build messages
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        # Prepare parameters.
        # NOTE: OpenAI gpt-5.* reasoning mode disallows sampling params such as
        # temperature/top_p/logprobs unless reasoning_effort="none". We enforce
        # this at the client layer to avoid hard-to-debug 400s at runtime.
        effective_reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else self.reasoning_effort
        )
        effective_verbosity = verbosity if verbosity is not None else self.verbosity
        effective_response_format = response_format

        # Prevent accidental overrides via **kwargs
        reserved = {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "max_completion_tokens",
            "reasoning_effort",
            "verbosity",
            "response_format",
        }
        if any(k in kwargs for k in reserved):
            logger.warning(
                "LLMClient.chat received reserved keys in kwargs and will ignore them: %s",
                sorted(set(kwargs.keys()) & reserved),
            )
            kwargs = {k: v for k, v in kwargs.items() if k not in reserved}

        params: Dict[str, Any] = {"model": self.model, "messages": messages}

        # Reasoning controls (OpenAI-only)
        if self.provider == "openai":
            if effective_reasoning_effort is not None:
                if not self._is_chat_param_unsupported("reasoning_effort"):
                    params["reasoning_effort"] = effective_reasoning_effort
            if effective_verbosity is not None:
                if not self._is_chat_param_unsupported("verbosity"):
                    params["verbosity"] = effective_verbosity
            if effective_response_format is not None:
                if not self._is_chat_param_unsupported("response_format"):
                    params["response_format"] = effective_response_format

        # Token limit mapping (OpenAI: max_completion_tokens; others: max_tokens)
        effective_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        if self.provider == "openai":
            params["max_completion_tokens"] = effective_max_tokens
        else:
            params["max_tokens"] = effective_max_tokens

        # Sampling params. For OpenAI + reasoning enabled, do NOT send them.
        effective_temperature = self.temperature if temperature is None else temperature
        reasoning_on = (self.provider == "openai") and self._is_reasoning_enabled(effective_reasoning_effort)
        if reasoning_on:
            # Drop any incompatible params that might be supplied in kwargs too.
            self._pop_any(kwargs, ("temperature", "top_p", "logprobs", "top_logprobs"))
            if temperature is not None:
                logger.info(
                    "reasoning_effort=%s is enabled; ignoring temperature override.",
                    effective_reasoning_effort,
                )
        else:
            params["temperature"] = effective_temperature

        # Merge remaining kwargs last (advanced usage)
        params.update(kwargs)

        # Make API call
        request_params: Dict[str, Any] = dict(params)
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(**request_params)
                content = response.choices[0].message.content or ""

                logger.debug(f"LLM response: {content[:100]}...")
                return content

            except TypeError as e:
                # Compatibility layer for older OpenAI SDK versions that don't
                # accept certain kwargs (e.g., 'verbosity').
                bad = self._parse_unexpected_kwarg(e)
                if bad and bad in request_params:
                    self._mark_chat_param_unsupported(bad)
                    request_params.pop(bad, None)
                    logger.warning(
                        "OpenAI SDK does not support chat.completions param '%s' "
                        "(provider=%s, model=%s). Retrying without it. "
                        "Fix options: upgrade openai SDK in the image, or set the param to null in config.",
                        bad,
                        self.provider,
                        self.model,
                    )
                    continue
                raise

            except Exception as e:
                # Some SDK versions / proxies reject newer parameters via HTTP 400
                # (instead of a Python TypeError). Retry once by removing the
                # offending param so production doesn't hard-fail.
                msg = str(e)
                if (
                    self.provider == "openai"
                    and "response_format" in request_params
                    and ("response_format" in msg or "json_schema" in msg or "json_object" in msg)
                ):
                    self._mark_chat_param_unsupported("response_format")
                    request_params.pop("response_format", None)
                    logger.warning(
                        "OpenAI API rejected response_format (provider=%s, model=%s). "
                        "Retrying without it. Error: %s",
                        self.provider,
                        self.model,
                        msg[:300],
                    )
                    continue

                logger.error(f"LLM API call failed: {e}")
                raise

        raise RuntimeError("LLM call failed after removing unsupported parameters")

    def __call__(self, prompt: str, **kwargs) -> str:
        """
        Shorthand for chat() method.

        Args:
            prompt: User prompt
            **kwargs: Additional parameters

        Returns:
            Generated text
        """
        return self.chat(prompt, **kwargs)


def create_llm_client_from_config(config: Dict[str, Any]) -> LLMClient:
    """
    Create LLM client from configuration dictionary.

    Args:
        config: Configuration dict with keys:
            - provider: "openai" or "dashscope"
            - model: Model name
            - temperature: Sampling temperature
            - max_tokens: Max tokens
            - api_key (optional): API key
            - base_url (optional): Custom base URL

    Returns:
        Configured LLMClient instance
    """
    return LLMClient(
        provider=config.get("provider", "dashscope"),
        model=config.get("model", "qwen-plus"),
        temperature=config.get("temperature", 0.7),
        max_tokens=config.get("max_tokens", 2000),
        reasoning_effort=config.get("reasoning_effort"),
        verbosity=config.get("verbosity"),
        api_key=config.get("api_key"),
        base_url=config.get("base_url")
    )


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Example 1: DashScope (Aliyun)
    try:
        client = LLMClient(
            provider="dashscope",
            model="qwen-plus",
            temperature=0.7
        )

        response = client.chat(
            prompt="What is Deep Eutectic Solvent?",
            system_prompt="You are a chemistry expert."
        )

        print("Response from DashScope:")
        print(response)

    except Exception as e:
        print(f"Error: {e}")

    # Example 2: OpenAI
    try:
        client = LLMClient(
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.7
        )

        response = client("Explain hydrogen bonding in one sentence.")

        print("\nResponse from OpenAI:")
        print(response)

    except Exception as e:
        print(f"Error: {e}")
