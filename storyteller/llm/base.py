"""
Common interface of the LLM providers.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

#############
# Constants #
#############

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

#############
# Functions #
#############


def strip_reasoning(text: str) -> str:
    """
    Remove the reasoning that some local models write before their answer.

    Parameters
    ----------
    text : str
        Model answer, possibly starting with <think>...</think>.

    Returns
    -------
    str
        Answer without the reasoning; empty when the answer was cut inside
        the reasoning.
    """
    text = _THINK_BLOCK.sub("", text)
    lowered = text.lower()
    if "</think>" in lowered:
        # Opening tag added by the chat template, not by the model
        text = text[lowered.rindex("</think>") + len("</think>"):]
    elif "<think>" in lowered:
        text = text[:lowered.index("<think>")]
    return text.strip()


###########
# Classes #
###########


class LLMError(Exception):
    """Raised when the provider cannot produce a text."""


@dataclass
class LLMResponse:
    """
    Text produced by a provider.

    Attributes
    ----------
    text : str
        Generated text.
    truncated : bool
        Whether the output limit was reached.
    model : str
        Model that answered.
    """

    text: str
    truncated: bool
    model: str


class LLMProvider:
    """
    Base class of the providers.

    Parameters
    ----------
    base_url : str
        API base URL.
    model : str
        Model identifier.
    api_key : str or None
        API key, None for local servers.
    temperature : float or None
        Sampling temperature, None to use the provider default.
    max_tokens : int
        Output token limit.
    timeout : float
        Request timeout, in seconds.
    should_stop : callable, optional
        Cancellation check.
    """

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None,
                 temperature: Optional[float] = None, max_tokens: int = 8000,
                 timeout: float = 300, should_stop: Optional[Callable[[], bool]] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or None
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.should_stop = should_stop

    def generate(self, system: str, user: str) -> LLMResponse:
        """
        Generate a text.

        Parameters
        ----------
        system : str
            System prompt.
        user : str
            User message.

        Returns
        -------
        LLMResponse
            Generated text.
        """
        raise NotImplementedError

    def list_models(self) -> List[str]:
        """
        List the models available with the current credentials.

        Returns
        -------
        list of str
            Model identifiers.
        """
        raise NotImplementedError
