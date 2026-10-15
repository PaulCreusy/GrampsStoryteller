"""
Provider for every API compatible with OpenAI chat completions: OpenAI,
Mistral, Groq, OpenRouter, Ollama, LM Studio...
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import List

# Local imports #
from ..http import HttpError, request_json
from .base import LLMError, LLMProvider, LLMResponse, strip_reasoning

###########
# Classes #
###########


class OpenAICompatibleProvider(LLMProvider):
    """
    Chat completions provider (POST {base_url}/chat/completions).
    """

    def _error(self, detail: str) -> LLMError:
        """
        Build an error, hinting at the "/v1" suffix when the URL looks wrong.

        Parameters
        ----------
        detail : str
            Error reported by the server.

        Returns
        -------
        LLMError
            Error to raise.
        """
        lowered = detail.lower()
        wrong_url = ("endpoint" in lowered or "not found" in lowered or "404" in lowered
                     or "unexpected" in lowered)
        if wrong_url and not self.base_url.rstrip("/").endswith("/v1"):
            detail += (" — the API URL usually ends with /v1 (e.g. http://localhost:1234/v1"
                       " for LM Studio).")
        return LLMError(detail)

    def _check_answer(self, answer: object) -> dict:
        """
        Raise when a server answers HTTP 200 with an error object.

        Parameters
        ----------
        answer : object
            Decoded answer.

        Returns
        -------
        dict
            The answer when it is not an error.

        Raises
        ------
        LLMError
            When the answer is an error or not an object.
        """
        if not isinstance(answer, dict):
            raise self._error("Unexpected answer: %s" % str(answer)[:300])
        error = answer.get("error")
        if error:
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise self._error(message)
        return answer

    def _headers(self) -> dict:
        """
        Build the authentication headers.

        Returns
        -------
        dict
            Headers.
        """
        return {"Authorization": "Bearer %s" % self.api_key} if self.api_key else {}

    def _payload(self, system: str, user: str, token_param: str, with_temperature: bool) -> dict:
        """
        Build the request body.

        Parameters
        ----------
        system : str
            System prompt.
        user : str
            User message.
        token_param : str
            "max_tokens" or "max_completion_tokens".
        with_temperature : bool
            Whether to send the temperature.

        Returns
        -------
        dict
            Body.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            token_param: self.max_tokens,
        }
        if with_temperature and self.temperature is not None:
            payload["temperature"] = self.temperature
        return payload

    def generate(self, system: str, user: str) -> LLMResponse:
        """
        Generate a text, adapting the request to models that reject the
        temperature or use max_completion_tokens.

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
            When the API fails or returns no text.
        """
        token_param = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
        with_temperature = True
        answer = None
        for _attempt in range(3):
            try:
                answer = request_json(
                    self.base_url + "/chat/completions", method="POST",
                    headers=self._headers(),
                    payload=self._payload(system, user, token_param, with_temperature),
                    timeout=self.timeout, should_stop=self.should_stop)
                break
            except HttpError as exc:
                message = str(exc).lower()
                if exc.status == 400 and "temperature" in message and with_temperature:
                    with_temperature = False
                elif exc.status == 400 and token_param in message:
                    token_param = ("max_tokens" if token_param == "max_completion_tokens"
                                   else "max_completion_tokens")
                else:
                    raise self._error(str(exc)) from exc
        if answer is None:
            raise LLMError("The API rejected the request parameters.")
        answer = self._check_answer(answer)

        try:
            choice = answer["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError("Unexpected answer: %s" % str(answer)[:300]) from exc
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = strip_reasoning(content)
        if not content.strip():
            raise LLMError("The model returned an empty text (finish reason: %s)."
                           % choice.get("finish_reason"))
        return LLMResponse(
            text=content.strip(),
            truncated=choice.get("finish_reason") == "length",
            model=answer.get("model") or self.model,
        )

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
            answer = request_json(self.base_url + "/models", headers=self._headers(),
                                  timeout=30, retries=1)
        except HttpError as exc:
            raise self._error(str(exc)) from exc
        answer = self._check_answer(answer)
        return sorted(item["id"] for item in answer.get("data", []) if "id" in item)
