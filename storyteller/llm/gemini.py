"""
Provider for the Google Gemini API.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import urllib.parse
from typing import List

# Local imports #
from ..http import HttpError, request_json
from .base import LLMError, LLMProvider, LLMResponse

###########
# Classes #
###########


class GeminiProvider(LLMProvider):
    """
    generateContent provider (POST {base_url}/models/{model}:generateContent).
    """

    def _headers(self) -> dict:
        """
        Build the authentication headers.

        Returns
        -------
        dict
            Headers.
        """
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

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
            When the API fails, blocks the prompt or returns no text.
        """
        config = {"maxOutputTokens": self.max_tokens}
        if self.temperature is not None:
            config["temperature"] = self.temperature
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        url = "%s/models/%s:generateContent" % (self.base_url, urllib.parse.quote(self.model))
        try:
            answer = request_json(url, method="POST", headers=self._headers(), payload=payload,
                                  timeout=self.timeout, should_stop=self.should_stop)
        except HttpError as exc:
            raise LLMError(str(exc)) from exc

        block_reason = answer.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            raise LLMError("The prompt was blocked (%s)." % block_reason)
        candidates = answer.get("candidates") or []
        if not candidates:
            raise LLMError("The model returned no candidate.")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if not text.strip():
            raise LLMError("The model returned an empty text (finish reason: %s)."
                           % candidate.get("finishReason"))
        return LLMResponse(
            text=text.strip(),
            truncated=candidate.get("finishReason") == "MAX_TOKENS",
            model=answer.get("modelVersion") or self.model,
        )

    def list_models(self) -> List[str]:
        """
        List the models supporting generateContent.

        Returns
        -------
        list of str
            Model identifiers (without the "models/" prefix).

        Raises
        ------
        LLMError
            When the API fails.
        """
        try:
            answer = request_json(self.base_url + "/models?pageSize=200", headers=self._headers(),
                                  timeout=30, retries=1)
        except HttpError as exc:
            raise LLMError(str(exc)) from exc
        models = []
        for item in answer.get("models", []):
            if "generateContent" in item.get("supportedGenerationMethods", []):
                models.append(item.get("name", "").replace("models/", "", 1))
        return sorted(m for m in models if m)
