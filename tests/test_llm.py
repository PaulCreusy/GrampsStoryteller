"""
Tests of the LLM providers with a fake HTTP layer.
"""

###########
# Imports #
###########

# Dependencies #
import pytest

# Local imports #
from storyteller.http import HttpError
from storyteller.llm import anthropic, gemini, openai_compat
from storyteller.llm.base import LLMError
from storyteller.llm.registry import PRESETS, create_provider

###########
# Classes #
###########


class FakeHttp:
    """
    Record requests and return queued answers (or raise queued errors).

    Parameters
    ----------
    answers : list
        Answers or exceptions, in order.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, method="GET", headers=None, payload=None, **kwargs):
        self.calls.append({"url": url, "method": method, "headers": headers or {},
                           "payload": payload})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


#############
# Functions #
#############


def test_openai_request_and_answer(monkeypatch):
    fake = FakeHttp([{"model": "gpt-x", "choices": [
        {"message": {"content": " Texte [1]. "}, "finish_reason": "stop"}]}])
    monkeypatch.setattr(openai_compat, "request_json", fake)
    provider = create_provider("openai", "", "", "sk-test", 0.3, 1000)
    response = provider.generate("sys", "user")

    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["payload"]["max_completion_tokens"] == 1000
    assert call["payload"]["temperature"] == 0.3
    assert call["payload"]["messages"][0] == {"role": "system", "content": "sys"}
    assert response.text == "Texte [1]."
    assert not response.truncated


def test_openai_retries_without_temperature(monkeypatch):
    error = HttpError("HTTP 400: Unsupported value: 'temperature' does not support 0.3", 400)
    fake = FakeHttp([error, {"choices": [{"message": {"content": "ok"},
                                          "finish_reason": "length"}]}])
    monkeypatch.setattr(openai_compat, "request_json", fake)
    provider = create_provider("mistral", "", "m", "key", 0.3, 500)
    response = provider.generate("s", "u")
    assert "temperature" in fake.calls[0]["payload"]
    assert "temperature" not in fake.calls[1]["payload"]
    assert fake.calls[1]["payload"]["max_tokens"] == 500
    assert response.truncated


def test_local_model_reasoning_is_removed(monkeypatch):
    fake = FakeHttp([
        {"choices": [{"message": {"content": "<think>Je réfléchis {x}.</think>\nTexte."},
                      "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "<think>Je réfléchis sans fin"},
                      "finish_reason": "length"}]},
    ])
    monkeypatch.setattr(openai_compat, "request_json", fake)
    provider = create_provider("lmstudio", "", "qwen", None, None, 100)
    assert provider.generate("s", "u").text == "Texte."
    with pytest.raises(LLMError, match="empty"):
        provider.generate("s", "u")


def test_local_server_needs_no_key(monkeypatch):
    fake = FakeHttp([{"data": [{"id": "b"}, {"id": "a"}]}])
    monkeypatch.setattr(openai_compat, "request_json", fake)
    provider = create_provider("ollama", "", "", None, None, 100)
    assert provider.list_models() == ["a", "b"]
    assert "Authorization" not in fake.calls[0]["headers"]
    assert not PRESETS["ollama"].needs_key


def test_anthropic_request_uses_fallback_and_no_temperature(monkeypatch):
    fake = FakeHttp([{"model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": ""}, {"type": "text", "text": "Contexte [1]."}]}])
    monkeypatch.setattr(anthropic, "request_json", fake)
    provider = create_provider("anthropic", "", "", "key", 0.3, 8000)
    response = provider.generate("sys", "user")

    call = fake.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "key"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["headers"]["anthropic-beta"] == anthropic.FALLBACK_BETA
    assert call["payload"]["fallbacks"] == "default"
    assert call["payload"]["system"] == "sys"
    assert "temperature" not in call["payload"]
    assert response.text == "Contexte [1]."


def test_anthropic_without_fallback_for_other_models(monkeypatch):
    fake = FakeHttp([{"stop_reason": "max_tokens", "content": [{"type": "text", "text": "a"}]}])
    monkeypatch.setattr(anthropic, "request_json", fake)
    provider = create_provider("anthropic", "", "claude-haiku-5-5", "key", None, 100)
    assert provider.generate("s", "u").truncated
    assert "fallbacks" not in fake.calls[0]["payload"]
    assert "anthropic-beta" not in fake.calls[0]["headers"]


def test_anthropic_refusal(monkeypatch):
    fake = FakeHttp([{"stop_reason": "refusal", "content": []}])
    monkeypatch.setattr(anthropic, "request_json", fake)
    provider = create_provider("anthropic", "", "", "key", None, 100)
    with pytest.raises(LLMError):
        provider.generate("s", "u")


def test_gemini_request(monkeypatch):
    fake = FakeHttp([{"candidates": [{"content": {"parts": [
        {"text": "pensée", "thought": True}, {"text": "Texte"}]}, "finishReason": "STOP"}]}])
    monkeypatch.setattr(gemini, "request_json", fake)
    provider = create_provider("gemini", "", "", "gkey", 0.2, 300)
    response = provider.generate("sys", "user")
    call = fake.calls[0]
    assert call["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert call["headers"]["x-goog-api-key"] == "gkey"
    assert call["payload"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert call["payload"]["generationConfig"] == {"maxOutputTokens": 300, "temperature": 0.2}
    assert response.text == "Texte"


def test_http_errors_become_llm_errors(monkeypatch):
    fake = FakeHttp([HttpError("HTTP 401: invalid key", 401)])
    monkeypatch.setattr(gemini, "request_json", fake)
    with pytest.raises(LLMError, match="401"):
        create_provider("gemini", "", "", "bad", None, 10).generate("s", "u")


def test_error_answer_suggests_v1_suffix(monkeypatch):
    fake = FakeHttp([{"error": "Unexpected endpoint or method. (POST /chat/completions)"}])
    monkeypatch.setattr(openai_compat, "request_json", fake)
    provider = create_provider("lmstudio", "http://localhost:1234", "m", None, None, 100)
    with pytest.raises(LLMError, match="/v1"):
        provider.generate("s", "u")
