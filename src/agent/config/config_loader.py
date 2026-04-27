"""
Configuration Loader for ReasoningBank Agent

Loads configuration from reasoningbank_config.yaml and provides type-safe access.
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """
    Load and manage configuration from YAML file.

    Supports:
    - Environment variable substitution
    - Default values
    - Type-safe accessors
    - Path resolution relative to project root
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize config loader.

        Args:
            config_path: Path to config YAML file.
                        If None, uses default: src/agent/config/reasoningbank_config.yaml
        """
        if config_path is None:
            config_path = os.getenv("AGENT_CONFIG_PATH")

        if config_path is None:
            # Default: find config relative to this file
            config_path = Path(__file__).parent / "reasoningbank_config.yaml"

        self.config_path = Path(config_path)

        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        # Load YAML
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # Determine project root (3 levels up from this file: config/ -> agent/ -> src/ -> root)
        self.project_root = Path(__file__).parent.parent.parent.parent

        logger.info(f"Loaded config from: {self.config_path}")
        logger.info(f"Project root: {self.project_root}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-notation key.

        Args:
            key: Key in dot notation (e.g., "llm.model")
            default: Default value if key not found

        Returns:
            Configuration value

        Example:
            >>> config = ConfigLoader()
            >>> model = config.get("llm.model")
            >>> top_k = config.get("memory.retrieval_top_k", 3)
        """
        keys = key.split('.')
        value = self.config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Get entire configuration section.

        Args:
            section: Section name (e.g., "llm", "memory")

        Returns:
            Configuration dictionary for that section

        Example:
            >>> config = ConfigLoader()
            >>> llm_config = config.get_section("llm")
            >>> print(llm_config["model"])
        """
        return self.config.get(section, {})

    def resolve_path(self, path: str) -> Path:
        """
        Resolve path relative to project root.

        Args:
            path: Relative path string (e.g., "data/memory/reasoning_bank.json")

        Returns:
            Absolute Path object

        Example:
            >>> config = ConfigLoader()
            >>> memory_path = config.resolve_path(config.get("memory.persist_path"))
        """
        path_obj = Path(path)

        # If already absolute, return as-is
        if path_obj.is_absolute():
            return path_obj

        # Otherwise, resolve relative to project root
        return self.project_root / path_obj

    # ===== Convenience accessors for common configs =====

    def get_llm_config(self, llm_type: str = "llm") -> Dict[str, Any]:
        """
        Get LLM configuration.

        Args:
            llm_type: "llm" (default) or "agent_llm"

        Returns:
            LLM configuration dict with keys:
            - provider: "dashscope" or "openai"
            - model: Model name
            - temperature: Sampling temperature
            - max_tokens: Max completion tokens
            - api_base: Optional custom API base URL
        """
        return self.get_section(llm_type)

    def get_embedding_config(self) -> Dict[str, Any]:
        """Get embedding configuration."""
        return self.get_section("embedding")

    def get_memory_config(self) -> Dict[str, Any]:
        """Get memory bank configuration."""
        return self.get_section("memory")

    def get_recommendations_config(self) -> Dict[str, Any]:
        """Get async recommendations configuration (NEW)."""
        return self.get_section("recommendations")

    def get_judge_config(self) -> Dict[str, Any]:
        """Get judge configuration."""
        return self.get_section("judge")

    def get_extractor_config(self) -> Dict[str, Any]:
        """Get extractor configuration."""
        return self.get_section("extractor")

    def get_tools_config(self) -> Dict[str, Any]:
        """Get tools configuration."""
        return self.get_section("tools")

    def get_agent_config(self) -> Dict[str, Any]:
        """Get agent behavior configuration."""
        return self.get_section("agent")

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return self.get_section("logging")


# Global singleton instance
_config_loader: Optional[ConfigLoader] = None


def get_config(config_path: Optional[str] = None) -> ConfigLoader:
    """
    Get global ConfigLoader instance (singleton pattern).

    Args:
        config_path: Optional path to config file. Only used on first call.

    Returns:
        Global ConfigLoader instance

    Example:
        >>> from agent.config import get_config
        >>> config = get_config()
        >>> model = config.get("llm.model")
    """
    global _config_loader

    if config_path is None:
        config_path = os.getenv("AGENT_CONFIG_PATH")

    if _config_loader is None:
        _config_loader = ConfigLoader(config_path)
    elif config_path is not None:
        requested_path = Path(config_path).expanduser().resolve()
        current_path = _config_loader.config_path.expanduser().resolve()
        if requested_path != current_path:
            logger.info(f"Reloading config from: {requested_path}")
            _config_loader = ConfigLoader(str(requested_path))

    return _config_loader


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Load config
    config = get_config()

    # Test accessors
    print("=== LLM Config ===")
    llm_config = config.get_llm_config()
    print(f"Provider: {llm_config['provider']}")
    print(f"Model: {llm_config['model']}")
    print(f"Temperature: {llm_config['temperature']}")

    print("\n=== Memory Config ===")
    memory_config = config.get_memory_config()
    print(f"Max items: {memory_config['max_items']}")
    print(f"Retrieval top-k: {memory_config['retrieval_top_k']}")
    print(f"Persist path: {config.resolve_path(memory_config['persist_path'])}")

    print("\n=== Recommendations Config ===")
    rec_config = config.get_recommendations_config()
    print(f"Storage dir: {config.resolve_path(rec_config['storage_dir'])}")

    print("\n=== Dot notation access ===")
    print(f"llm.model = {config.get('llm.model')}")
    print(f"memory.retrieval_top_k = {config.get('memory.retrieval_top_k')}")
    print(f"recommendations.storage_dir = {config.get('recommendations.storage_dir')}")
