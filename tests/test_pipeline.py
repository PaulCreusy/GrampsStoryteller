"""
Tests of the generation pipeline, settings and credentials.
"""

###########
# Imports #
###########

# Python imports #
import os
import stat

# Dependencies #
import pytest

# Local imports #
from conftest import FakeWikipedia
from storyteller import credentials
from storyteller.anonymizer import build_request
from storyteller.cache import DiskCache
from storyteller.config import Settings
from storyteller.llm.base import LLMError, LLMProvider, LLMResponse
from storyteller.models import Landmark, PersonFacts, PlaceInfo
from storyteller.pipeline import StoryPipeline

###########
# Classes #
###########


class FakeProvider(LLMProvider):
    """
    Provider returning queued texts and recording the prompts.

    Parameters
    ----------
    texts : list of str
        Answers, in order.
    """

    def __init__(self, texts):
        super().__init__("http://fake", "fake-model")
        self.texts = list(texts)
        self.prompts = []

    def generate(self, system, user):
        self.prompts.append((system, user))
        return LLMResponse(text=self.texts.pop(0), truncated=False, model=self.model)


#############
# Constants #
#############

PAGES = {
    "Brest (Finistère)": "Brest est une commune du Finistère.\n== Histoire ==\n"
                         "En 1830, l'arsenal emploie des milliers d'ouvriers.",
    "Monarchie de Juillet": "La monarchie de Juillet dure de 1830 à 1848.",
}

#############
# Functions #
#############


def _request():
    place = PlaceInfo(name="Brest", country="France", code="29200")
    facts = PersonFacts(handle="h", gramps_id="I7", name="Anne Kerbrat",
                        landmarks=[Landmark(year=1832, place=place)])
    return build_request(facts, "fr")


def test_generation_uses_sources_and_cache(isolated_dirs):
    settings = Settings()
    cache = DiskCache(str(isolated_dirs / "cache"))
    provider = FakeProvider(["En 1830, l'arsenal emploie des milliers d'ouvriers [1]."])
    pipeline = StoryPipeline(settings, cache=cache, wiki_client=FakeWikipedia(PAGES),
                             provider=provider)
    result = pipeline.generate(_request())

    system, user = provider.prompts[0]
    assert "1832 – Brest (Finistère, France)" in user
    assert "[1] Brest (Finistère) (commune)" in user
    assert "Anne" not in user + system and "I7" not in user + system
    assert "29200" not in user
    assert "{" not in user.replace("{}", "")
    assert "\n\n\n" not in user
    assert result.validation.ok
    assert not result.from_cache
    assert result.sources.extracts[0].number == 1

    again = StoryPipeline(settings, cache=cache, wiki_client=FakeWikipedia(PAGES),
                          provider=FakeProvider([])).generate(_request())
    assert again.from_cache
    assert again.text == result.text


def test_llm_validation_is_optional(isolated_dirs):
    settings = Settings()
    provider = FakeProvider(["Texte [1].", '{"ok": false, "problemes": ["Phrase 2 inventée"]}'])
    pipeline = StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                             provider=provider)
    result = pipeline.generate(_request())
    assert len(provider.prompts) == 1
    assert not result.validation.llm_checked

    settings["llm_validation"] = True
    provider = FakeProvider(["Texte [1].", '{"ok": false, "problemes": ["Phrase 2 inventée"]}'])
    pipeline = StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                             provider=provider)
    result = pipeline.generate(_request())
    assert len(provider.prompts) == 2
    assert "Texte [1]." in provider.prompts[1][1]
    assert "1832 – Brest (Finistère, France)" in provider.prompts[1][1]
    assert result.validation.llm_checked
    assert result.validation.llm_problems == 1
    assert any("Phrase 2 inventée" in w for w in result.validation.warnings)


def test_unreadable_validation_is_explained(isolated_dirs):
    settings = Settings()
    settings["llm_validation"] = True
    provider = FakeProvider(["Texte.", "Je ne sais pas répondre en JSON."])
    result = StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                           provider=provider).generate(_request())
    assert any("Je ne sais pas répondre en JSON." in w for w in result.validation.warnings)


def test_auto_correction_rewrites_the_flagged_text(isolated_dirs):
    settings = Settings()
    settings["llm_validation"] = True
    settings["auto_correction"] = True
    provider = FakeProvider([
        "Texte initial inventé.",
        '{"ok": false, "problemes": ["« inventé » : absent des extraits"]}',
        "Texte corrigé.",
        '{"ok": true, "problemes": []}',
    ])
    pipeline = StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                             provider=provider)
    result = pipeline.generate(_request())
    assert len(provider.prompts) == 4
    system, user = provider.prompts[2]
    assert system == provider.prompts[0][0]  # same writing rules
    assert "« inventé » : absent des extraits" in user
    assert "Texte initial inventé." in user
    assert "Texte corrigé." in provider.prompts[3][1]
    assert result.text == "Texte corrigé."
    assert result.original_text == "Texte initial inventé."
    assert result.validation.corrected_problems == ["« inventé » : absent des extraits"]
    assert result.validation.llm_problems == 0


def test_failed_correction_keeps_the_initial_text(isolated_dirs):
    settings = Settings()
    settings["llm_validation"] = True
    settings["auto_correction"] = True
    provider = FakeProvider(["Texte initial.", '{"ok": false, "problemes": ["Phrase 1"]}', " "])
    result = StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                           provider=provider).generate(_request())
    assert result.text == "Texte initial."
    assert result.original_text is None
    assert result.validation.llm_problems == 1
    assert any("correction" in w for w in result.validation.warnings)

    # Without problems, no correction request is sent
    provider = FakeProvider(["Texte.", '{"ok": true, "problemes": []}'])
    StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                  provider=provider).generate(_request())
    assert not provider.texts and len(provider.prompts) == 2


def test_custom_prompts_are_used(isolated_dirs):
    settings = Settings()
    settings["user_prompt"] = "R={reperes} L={langue} N={longueur} J={\"json\": 1}"
    provider = FakeProvider(["ok"])
    StoryPipeline(settings, cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES),
                  provider=provider).generate(_request())
    assert provider.prompts[0][1] == ("R=- 1832 – Brest (Finistère, France) L=français N=250 "
                                      "J={\"json\": 1}")


def test_missing_key_is_reported(isolated_dirs):
    pipeline = StoryPipeline(Settings(), cache=DiskCache(None), wiki_client=FakeWikipedia(PAGES))
    with pytest.raises(LLMError, match="clé"):
        pipeline.generate(_request())


def test_settings_round_trip(isolated_dirs):
    settings = Settings.load()
    assert settings["language"] == "fr"
    settings["provider"] = "anthropic"
    settings.set_provider_setting("anthropic", "model", "claude-sonnet-5-5")
    settings.save()
    loaded = Settings.load()
    assert loaded["provider"] == "anthropic"
    assert loaded.provider_setting("anthropic", "model") == "claude-sonnet-5-5"
    assert loaded.provider_setting("openai", "base_url") == "https://api.openai.com/v1"


def test_credentials_file_and_env(isolated_dirs, monkeypatch):
    assert credentials.get_api_key("openai") is None
    assert credentials.set_api_key("openai", " sk-1 ") == "file"
    assert credentials.get_api_key("openai") == "sk-1"
    assert credentials.key_source("openai") == "file"
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(credentials.credentials_file()).st_mode)
        assert mode == 0o600
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert credentials.get_api_key("openai") == "sk-env"
    assert credentials.key_source("openai") == "env"
    monkeypatch.delenv("OPENAI_API_KEY")
    credentials.set_api_key("openai", "")
    assert credentials.get_api_key("openai") is None
