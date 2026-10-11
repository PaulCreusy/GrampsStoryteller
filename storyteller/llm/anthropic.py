"""
Provider for the Claude Messages API of Anthropic.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import List

# Local imports #
from ..http import HttpError, request_json
from .base import LLMError, LLMProvider, LLMResponse

#############
# Constants #
#############

API_VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models accepting the server-side fallback on a refusal
FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")

###########
# Classes #
###########


class AnthropicProvider(LLMProvider):
    """
    Messages API provider (POST {base_url}/messages).

    The temperature is never sent: recent Claude models reject it.
    """

    def _headers(self, with_fallback: bool = False) -> dict:
        """
        Build the request headers.

        Parameters
        ----------
        with_fallback : bool
            Whether to enable the server-side fallback beta.

        Returns
        -------
        dict
            Headers.
        """
        headers = {"anthropic-version": API_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        if with_fallback:
            headers["anthropic-beta"] = FALLBACK_BETA
        return headers

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

        Raises
        ------
        LLMError
            When the API fails, refuses or returns no text.
        """
        with_fallback = self.model.startswith(FALLBACK_MODEL_PREFIXES)
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            answer = self._send(payload, with_fallback)
        except HttpError as exc:
            if not (with_fallback and exc.status == 400 and "fallback" in str(exc).lower()):
                raise LLMError(str(exc)) from exc
            try:
                answer = self._send(payload, False)
            except HttpError as retry_exc:
                raise LLMError(str(retry_exc)) from retry_exc

        if answer.get("stop_reason") == "refusal":
            raise LLMError("The model declined the request (refusal).")
        text = "".join(block.get("text", "") for block in answer.get("content", [])
                       if block.get("type") == "text")
        if not text.strip():
            raise LLMError("The model returned an empty text (stop reason: %s)."
                           % answer.get("stop_reason"))
        return LLMResponse(
            text=text.strip(),
            truncated=answer.get("stop_reason") == "max_tokens",
            model=answer.get("model") or self.model,
        )

    def _send(self, payload: dict, with_fallback: bool) -> dict:
        """
        Send a Messages request.

        Parameters
        ----------
        payload : dict
            Body without the fallback field.
        with_fallback : bool
            Whether to enable the server-side fallback.

        Returns
        -------
        dict
            Decoded answer.
        """
        body = dict(payload, fallbacks="default") if with_fallback else payload
        return request_json(self.base_url + "/messages", method="POST",
                            headers=self._headers(with_fallback), payload=body,
                            timeout=self.timeout, should_stop=self.should_stop)

    def list_models(self) -> List[str]:
        """
        List the models (GET {base_url}/models).

        Returns
        -------
        list of str
            Model identifiers.

        Raises
        ------
        LLMError
            When the API fails.
        """
        try:
            answer = request_json(self.base_url + "/models?limit=100", headers=self._headers(),
                                  timeout=30, retries=1)
        except HttpError as exc:
            raise LLMError(str(exc)) from exc
        return [item["id"] for item in answer.get("data", []) if "id" in item]
