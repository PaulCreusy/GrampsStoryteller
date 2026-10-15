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
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# Local imports #
from . import prompts
from .cache import DiskCache
from .config import Settings, cache_dir
from .credentials import get_api_key
from .llm.base import LLMError, LLMProvider
from .llm.registry import PRESETS, create_provider
from .models import CollectedSources, ContextRequest, GenerationResult, ValidationReport
from .sources.hierarchy import SourceCollector
from .sources.wikipedia import WikipediaClient
from .validation import (
    answer_excerpt, check_locally, drop_unfounded_cg_problems, messages, parse_llm_validation,
    repair_cg_tags, tags_balanced, tidy_problem)

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
    variables : dict
        Template variables, reused by the validation and correction prompts.
    """

    system: str
    user: str
    sources: CollectedSources
    variables: Dict[str, str] = field(default_factory=dict)


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
        return PreparedPrompt(system=system, user=user, sources=sources, variables=variables)

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
            text, truncated, model = (repair_cg_tags(cached["text"]), cached["truncated"],
                                      cached["model"])
        else:
            self.progress(provider.model)
            response = provider.generate(prepared.system, prepared.user)
            text, truncated, model = (repair_cg_tags(response.text), response.truncated,
                                      response.model)
            self.cache.set("llm", cache_key, {"text": text, "truncated": truncated, "model": model})

        report = self._check(text, truncated, request, prepared)
        original_text = None
        if report.ok and self.settings["llm_validation"]:
            problems = self._validate_with_llm(text, prepared, report)
            if problems and self.settings["auto_correction"] and not tags_balanced(text):
                # The validation cannot tell sourced from general knowledge:
                # a correction would delete general knowledge taken for sourced
                report.warnings.append(messages(self.lang)["correction_skipped"])
            elif problems and self.settings["auto_correction"]:
                corrected = self._correct(text, problems, request, prepared, report)
                if corrected is not None:
                    original_text, (text, report) = text, corrected

        return GenerationResult(text=text, sources=prepared.sources, validation=report,
                                provider_label=self.provider_label, model=model,
                                from_cache=bool(cached), original_text=original_text)

    def _check(self, text: str, truncated: bool, request: ContextRequest,
               prepared: PreparedPrompt) -> ValidationReport:
        """
        Run the local checks of a text.

        Parameters
        ----------
        text : str
            Generated text.
        truncated : bool
            Whether the output limit was reached.
        request : ContextRequest
            Anonymized request.
        prepared : PreparedPrompt
            Prompts and sources.

        Returns
        -------
        ValidationReport
            Local checks outcome.
        """
        extracts = prepared.sources.extracts
        allowed_years = {y for y in (request.year_min, request.year_max) if y is not None}
        return check_locally(text, len(extracts), "\n".join(e.text for e in extracts),
                             allowed_years, prepared.sources.rich, truncated, self.lang)

    def _language_name(self) -> str:
        """
        Return the name of the text language, written in that language.

        Returns
        -------
        str
            Language name.
        """
        lang = self.lang if self.lang in prompts.LANGUAGE_NAMES else "en"
        return prompts.LANGUAGE_NAMES[lang][lang]

    def _validate_with_llm(self, text: str, prepared: PreparedPrompt,
                           report: ValidationReport) -> List[str]:
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

        Returns
        -------
        list of str
            Problems found (empty when the answer was unreadable or the call
            failed).
        """
        texts = messages(self.lang)
        template = prompts.template_or_default(self.settings["validation_prompt"], "validation",
                                               self.lang)
        user = prompts.render(template, dict(prepared.variables, texte=text,
                                             langue=self._language_name()))
        self.progress("validation")
        try:
            response = self.provider.generate(
                _VALIDATION_SYSTEM.get(self.lang, _VALIDATION_SYSTEM["en"]), user)
        except LLMError as exc:
            report.warnings.append(texts["llm_problem"] % exc)
            return []
        report.llm_checked = True
        parsed = parse_llm_validation(response.text)
        if parsed is None:
            report.warnings.append(texts["llm_truncated"] if response.truncated
                                   else texts["llm_invalid"] % answer_excerpt(response.text))
            report.llm_problems += 1
            return []
        problems = [tidy_problem(p, self.lang)
                    for p in drop_unfounded_cg_problems(text, parsed[1])]
        report.llm_problems += len(problems)
        for problem in problems:
            report.warnings.append(texts["llm_problem"] % problem)
        return problems

    def _correct(self, text: str, problems: List[str], request: ContextRequest,
                 prepared: PreparedPrompt,
                 report: ValidationReport) -> Optional[Tuple[str, ValidationReport]]:
        """
        Rewrite the text to fix the problems found by the LLM validation,
        then check the new text again.

        Parameters
        ----------
        text : str
            Generated text.
        problems : list of str
            Problems found by the validation.
        request : ContextRequest
            Anonymized request.
        prepared : PreparedPrompt
            Prompts and sources.
        report : ValidationReport
            Report of the initial text, completed when the correction fails.

        Returns
        -------
        tuple of (str, ValidationReport) or None
            Corrected text and its report, None when the initial text must be
            kept.
        """
        texts = messages(self.lang)
        template = prompts.template_or_default(self.settings["correction_prompt"], "correction",
                                               self.lang)
        user = prompts.render(template, dict(
            prepared.variables, texte=text, langue=self._language_name(),
            problemes="\n".join("- %s" % problem for problem in problems)))
        self.progress("correction")
        try:
            response = self.provider.generate(prepared.system, user)
        except LLMError as exc:
            report.warnings.append(texts["correction_failed"] % exc)
            return None
        corrected = repair_cg_tags(response.text)
        new_report = self._check(corrected, response.truncated, request, prepared)
        if not new_report.ok or response.truncated:
            reason = "; ".join(new_report.errors) or texts["truncated"]
            report.warnings.append(texts["correction_failed"] % reason)
            return None
        if tags_balanced(text) and not tags_balanced(corrected):
            # General knowledge would be presented as sourced
            report.warnings.append(texts["correction_failed"] % texts["correction_tags"])
            return None
        new_report.corrected_problems = list(problems)
        # Check the corrected text again so that the report tells what remains
        self._validate_with_llm(corrected, prepared, new_report)
        return corrected, new_report
