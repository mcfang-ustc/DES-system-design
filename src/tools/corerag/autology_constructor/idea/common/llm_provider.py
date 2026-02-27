import os
from langchain_openai import ChatOpenAI
from langchain_community.chat_models.tongyi import ChatTongyi
# from langchain_anthropic import ChatAnthropic

from typing import Any, Dict, Optional


try:
    from config.settings import LLM_CONFIG, OPENAI_API_KEY
except ImportError as e:
    print(f"Error: Could not import configuration from config.settings: {e}. ")

class ReasoningCompatibleChatOpenAI(ChatOpenAI):
    """
    ChatOpenAI always includes `temperature` in its request payload.

    OpenAI GPT-5.* models raise an error when `reasoning_effort` is enabled
    (i.e., not "none") *and* sampling params like `temperature/top_p/logprobs`
    are present. We therefore strip these fields from the payload whenever
    reasoning is enabled.
    """

    @property
    def _default_params(self) -> Dict[str, Any]:
        params = super()._default_params

        reasoning_effort = params.get("reasoning_effort")
        if reasoning_effort is None and hasattr(self, "model_kwargs"):
            reasoning_effort = (self.model_kwargs or {}).get("reasoning_effort")

        if reasoning_effort is not None and str(reasoning_effort).strip().lower() != "none":
            # GPT-5.* parameter compatibility: remove unsupported sampling params.
            params.pop("temperature", None)
            params.pop("top_p", None)
            params.pop("logprobs", None)
            params.pop("top_logprobs", None)

        return params


def get_default_llm():
    """Instantiates and returns the default LLM based on configuration."""
    model_name = LLM_CONFIG.get('model', 'gpt-4.1-mini')
    temperature = LLM_CONFIG.get('temperature', 0)
    openai_api_key_to_use = OPENAI_API_KEY if OPENAI_API_KEY and OPENAI_API_KEY != "default_api_key" else None

    if model_name:
        if not openai_api_key_to_use:
            # Check env var as a last resort if needed, or raise error
            openai_api_key_to_use = os.getenv("OPENAI_API_KEY")
            if not openai_api_key_to_use:
                 raise ValueError("OpenAI API Key is not configured in  environment variables.")
        # Extract openai_api_base if present in config.
        # Keep model/temperature as explicit args, and handle OpenAI-only fields
        # (reasoning_effort/verbosity) via model_kwargs.
        llm_params = {
            k: v
            for k, v in LLM_CONFIG.items()
            if k not in ['model', 'temperature', 'reasoning_effort', 'verbosity']
        }

        # Rename openai_api_base to base_url for newer ChatOpenAI versions
        if 'openai_api_base' in llm_params:
            llm_params['base_url'] = llm_params.pop('openai_api_base')

        # OpenAI-only reasoning knobs (safe to ignore for non-OpenAI endpoints)
        reasoning_effort = LLM_CONFIG.get("reasoning_effort")
        verbosity = LLM_CONFIG.get("verbosity")

        # Ensure we keep any existing model_kwargs (if provided) and append.
        model_kwargs: Dict[str, Any] = llm_params.get("model_kwargs") or {}
        if reasoning_effort is not None:
            model_kwargs["reasoning_effort"] = reasoning_effort
        if verbosity is not None:
            model_kwargs["verbosity"] = verbosity
        if model_kwargs:
            llm_params["model_kwargs"] = model_kwargs

        return ReasoningCompatibleChatOpenAI(
            model_name=model_name,
            temperature=temperature,
            openai_api_key=openai_api_key_to_use,
            **llm_params
        )
    else:
        raise ValueError(f"Only support OpenAI models, model name specified in LLM_CONFIG: {model_name}")

def get_qwen_llm():
    # return ChatOllama(
    #         model="myaniu/qwen2.5-1m:14b",
    #         base_url="https://30a6-36-5-153-246.ngrok-free.app",
    #         temperature=0,
    #         max_tokens=8192,
    #     )
    return ChatTongyi(
            model_name="qwen3-14b",
            model_kwargs={
                "temperature": 0,
                "enable_thinking": False,
                "max_tokens": 8192,
            }
        )
# Cached instance logic
DEFAULT_LLM_INSTANCE = None
def get_cached_default_llm(qwen=False):
    """Returns a cached instance of the default LLM."""
    global DEFAULT_LLM_INSTANCE
    if DEFAULT_LLM_INSTANCE is None:
        if qwen:
            DEFAULT_LLM_INSTANCE = get_qwen_llm()
        else:
            DEFAULT_LLM_INSTANCE = get_default_llm()
    return DEFAULT_LLM_INSTANCE 
