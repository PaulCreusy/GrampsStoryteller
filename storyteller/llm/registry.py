"""
Provider presets and factory.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

# Local imports #
from .anthropic import AnthropicProvider
from .base import LLMProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatibleProvider

###########
# Classes #
###########


@dataclass(frozen=True)
class ProviderPreset:
    """
    Default settings of a provider.

    Attributes
    ----------
    label : str
        Human readable name.
    kind : str
        "openai", "anthropic" or "gemini".
    base_url : str
        Default API base URL.
    default_model : str
        Suggested model.
    env_var : str or None
        Environment variable holding the API key.
    needs_key : bool
        Whether an API key is required.
    """

    label: str
    kind: str
    base_url: str
    default_model: str
    env_var: Optional[str] = None
    needs_key: bool = True


#############
# Constants #
#############

PRESETS = OrderedDict([
    ("openai", ProviderPreset("OpenAI", "openai", "https://api.openai.com/v1",
                              "gpt-5-mini", "OPENAI_API_KEY")),
    ("anthropic", ProviderPreset("Anthropic (Claude)", "anthropic", "https://api.anthropic.com/v1",
                                 "claude-opus-5-5", "ANTHROPIC_API_KEY")),
    ("gemini", ProviderPreset("Google Gemini", "gemini",
                              "https://generativelanguage.googleapis.com/v1beta",
                              "gemini-2.5-flash", "GEMINI_API_KEY")),
    ("mistral", ProviderPreset("Mistral AI", "openai", "https://api.mistral.ai/v1",
                               "mistral-medium-latest", "MISTRAL_API_KEY")),
    ("openrouter", ProviderPreset("OpenRouter", "openai", "https://openrouter.ai/api/v1",
                                  "openai/gpt-5-mini", "OPENROUTER_API_KEY")),
    ("groq", ProviderPreset("Groq", "openai", "https://api.groq.com/openai/v1",
                            "llama-3.3-70b-versatile", "GROQ_API_KEY")),
    ("ollama", ProviderPreset("Ollama (local)", "openai", "http://localhost:11434/v1",
                              "llama3.1", None, needs_key=False)),
    ("lmstudio", ProviderPreset("LM Studio (local)", "openai", "http://localhost:1234/v1",
                                "", None, needs_key=False)),
    ("custom", ProviderPreset("Custom (OpenAI compatible)", "openai", "http://localhost:8000/v1",
                              "", None, needs_key=False)),
])

_CLASSES = {
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

#############
# Functions #
#############


def create_provider(provider_id: str, base_url: str, model: str, api_key: Optional[str],
                    temperature: Optional[float], max_tokens: int,
                    should_stop: Optional[Callable[[], bool]] = None) -> LLMProvider:
    """
    Instantiate a provider from its preset identifier.

    Parameters
    ----------
    provider_id : str
        Key of PRESETS.
    base_url : str
        API base URL (empty for the preset default).
    model : str
        Model identifier (empty for the preset default).
    api_key : str or None
        API key.
    temperature : float or None
        Sampling temperature.
    max_tokens : int
        Output token limit.
    should_stop : callable, optional
        Cancellation check.

    Returns
    -------
    LLMProvider
        Provider instance.
    """
    preset = PRESETS.get(provider_id, PRESETS["custom"])
    cls = _CLASSES[preset.kind]
    return cls(base_url or preset.base_url, model or preset.default_model, api_key=api_key,
               temperature=temperature, max_tokens=max_tokens, should_stop=should_stop)
