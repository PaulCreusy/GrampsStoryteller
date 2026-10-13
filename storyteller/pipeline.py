"""
Generation pipeline, independent of Gramps and of the GUI:
anonymized request -> Wikipedia sources -> prompt -> LLM -> checks.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

# Local imports #
from . import prompts
from .cache import DiskCache
from .config import Settings, cache_dir
from .credentials import get_api_key
from .llm.base import LLMError, LLMProvider
from .llm.registry import PRESETS, create_provider
from .models import CollectedSources, ContextRequest, GenerationResult
from .sources.hierarchy import SourceCollector
from .sources.wikipedia import WikipediaClient
from .validation import check_locally, messages, parse_llm_validation

#############
# Constants #
#############

_VALIDATION_SYSTEM = {
    "fr": "Tu es un relecteur rigoureux et factuel.",
    "en": "You are a rigorous and factual proofreader.",
}

_MISSING_KEY = {
    "fr": "Aucune clé d'API n'est configurée pour %s : renseignez-la dans les paramètres.",
    "en": "No API key is configured for %s: set it in the settings.",
}

###########
# Classes #
###########


@dataclass
class PreparedPrompt:
    """
    Prompt ready to be sent.

    Attributes
    ----------
    system : str
        System prompt.
    user : str
        User message.
    sources : CollectedSources
        Excerpts used in the message.
    """

    system: str
    user: str
    sources: CollectedSources


class StoryPipeline:
    """
    Run the generation for one anonymized request.

    Parameters
    ----------
    settings : Settings
        User settings.
    should_stop : callable, optional
        Cancellation check.
    progress : callable, optional
        Called with short status messages.
    cache : DiskCache, optional
        Cache; defaults to the user cache directory.
    wiki_client : WikipediaClient, optional
        Wikipedia client (tests).
    provider : LLMProvider, optional
        LLM provider (tests); built from the settings by default.
    """

    def __init__(self, settings: Settings, should_stop: Optional[Callable[[], bool]] = None,
                 progress: Optional[Callable[[str], None]] = None,
                 cache: Optional[DiskCache] = None,
                 wiki_client: Optional[WikipediaClient] = None,
                 provider: Optional[LLMProvider] = None):
        self.settings = settings
        self.lang = settings["language"]
        self.progress = progress or (lambda message: None)
        self.cache = cache if cache is not None else DiskCache(cache_dir())
        self.wiki = wiki_client or WikipediaClient(self.lang, self.cache, should_stop)
        self.collector = SourceCollector(self.wiki, progress=self.progress)
        self.provider_id = settings["provider"]
        self.provider_label = PRESETS.get(self.provider_id, PRESETS["custom"]).label
        self._provider = provider
        self._should_stop = should_stop

    @property
    def provider(self) -> LLMProvider:
        """
        Return the LLM provider, created on first use.

        Returns
        -------
        LLMProvider
            Provider.

        Raises
        ------
        LLMError
            When the provider needs an API key that is not configured.
        """
        if self._provider is None:
            preset = PRESETS.get(self.provider_id, PRESETS["custom"])
            api_key = get_api_key(self.provider_id)
            if preset.needs_key and not api_key:
                raise LLMError(_MISSING_KEY.get(self.lang, _MISSING_KEY["en"]) % preset.label)
            temperature = self.settings["temperature"] if self.settings["send_temperature"] else None
            self._provider = create_provider(
                self.provider_id,
                self.settings.provider_setting(self.provider_id, "base_url"),
                self.settings.provider_setting(self.provider_id, "model"),
                api_key, temperature, int(self.settings["max_tokens"]),
                should_stop=self._should_stop)
        return self._provider

    def prepare(self, request: ContextRequest) -> PreparedPrompt:
        """
        Collect the sources and render the prompts.

        Parameters
        ----------
        request : ContextRequest
            Anonymized request.

        Returns
        -------
        PreparedPrompt
            Prompts and sources.
        """
        sources = self.collector.collect(request)
        variables = prompts.build_variables(request, sources.extracts, sources.rich, self.lang,
                                            int(self.settings["length_words"]))
        system = prompts.render(
            prompts.template_or_default(self.settings["system_prompt"], "system", self.lang),
            variables)
        user = prompts.render(
            prompts.template_or_default(self.settings["user_prompt"], "user", self.lang),
            variables)
        return PreparedPrompt(system=system, user=user, sources=sources)

    def generate(self, request: ContextRequest, use_cache: bool = True) -> GenerationResult:
        """
        Produce and check the context of a request.

        Parameters
        ----------
        request : ContextRequest
            Anonymized request.
        use_cache : bool
            Whether a previously generated text with the same prompt may be
            reused.

        Returns
        -------
        GenerationResult
            Text, sources and checks.

        Raises
        ------
        LLMError
            When the generation fails.
        """
        prepared = self.prepare(request)
        provider = self.provider
        cache_key = json.dumps([self.provider_id, provider.base_url, provider.model,
                                prepared.system, prepared.user])
        cached = self.cache.get("llm", cache_key) if use_cache else None
        if cached:
            text, truncated, model = cached["text"], cached["truncated"], cached["model"]
        else:
            self.progress(provider.model)
            response = provider.generate(prepared.system, prepared.user)
            text, truncated, model = response.text, response.truncated, response.model
            self.cache.set("llm", cache_key, {"text": text, "truncated": truncated, "model": model})

        extracts = prepared.sources.extracts
        allowed_years = {y for y in (request.year_min, request.year_max) if y is not None}
        report = check_locally(text, len(extracts), "\n".join(e.text for e in extracts),
                               allowed_years, prepared.sources.rich, truncated, self.lang)
        if report.ok and self.settings["llm_validation"]:
            self._validate_with_llm(text, prepared, report)

        return GenerationResult(text=text, sources=prepared.sources, validation=report,
                                provider_label=self.provider_label, model=model,
                                from_cache=bool(cached))

    def _validate_with_llm(self, text: str, prepared: PreparedPrompt, report):
        """
        Run the optional LLM validation and add its findings as warnings.

        Parameters
        ----------
        text : str
            Generated text.
        prepared : PreparedPrompt
            Prompts and sources.
        report : ValidationReport
            Report to complete.
        """
        texts = messages(self.lang)
        template = prompts.template_or_default(self.settings["validation_prompt"], "validation",
                                               self.lang)
        user = prompts.render(template, {
            "sources": prompts.format_sources(prepared.sources.extracts, self.lang),
            "texte": text,
            "langue": prompts.LANGUAGE_NAMES.get(self.lang, prompts.LANGUAGE_NAMES["en"])[
                self.lang if self.lang in ("fr", "en") else "en"],
        })
        self.progress("validation")
        try:
            response = self.provider.generate(
                _VALIDATION_SYSTEM.get(self.lang, _VALIDATION_SYSTEM["en"]), user)
        except LLMError as exc:
            report.warnings.append(texts["llm_problem"] % exc)
            return
        report.llm_checked = True
        parsed = parse_llm_validation(response.text)
        if parsed is None:
            report.warnings.append(texts["llm_invalid"])
            report.llm_problems += 1
            return
        report.llm_problems += len(parsed[1])
        for problem in parsed[1]:
            report.warnings.append(texts["llm_problem"] % problem)
