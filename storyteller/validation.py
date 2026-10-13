"""
Checks of the generated text.

The local checks are free and always run. The LLM validation is optional and
disabled by default because it costs a second request.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import json
import re
from typing import List, Optional, Set, Tuple

# Local imports #
from .models import ValidationReport

#############
# Constants #
#############

_CITATION = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
_CG_SPLIT = re.compile(r"(</?cg>)", re.IGNORECASE)
_YEAR = re.compile(r"(?<![\d.,])(1[0-9]{3}|20[0-9]{2})(?!\d)")
_URL = re.compile(r"https?://\S+")

_MESSAGES = {
    "fr": {
        "empty": "Le texte généré est vide.",
        "truncated": "Le texte a été tronqué par la limite de tokens : augmentez « Tokens max ».",
        "unknown_ref": "Références à des sources inexistantes : %s.",
        "unbalanced": "Balises <cg> mal fermées : le marquage des connaissances générales est "
                      "approximatif.",
        "cg_count": "%d passage(s) de connaissances générales (non sourcées), signalés en "
                    "italique dans la note.",
        "years": "Années citées comme sourcées mais absentes des extraits : %s.",
        "url": "Le texte contient des URL, elles ont été retirées.",
        "llm_invalid": "La validation par LLM a renvoyé une réponse illisible.",
        "llm_problem": "Validation LLM : %s",
        "correction_failed": "La correction automatique a échoué (%s) : le texte initial est "
                             "conservé.",
    },
    "en": {
        "empty": "The generated text is empty.",
        "truncated": "The text was cut by the token limit: increase \"Max tokens\".",
        "unknown_ref": "References to missing sources: %s.",
        "unbalanced": "Unbalanced <cg> tags: the general knowledge marking is approximate.",
        "cg_count": "%d general knowledge passage(s) (unsourced), shown in italics in the note.",
        "years": "Years cited as sourced but missing from the excerpts: %s.",
        "url": "The text contained URLs, they were removed.",
        "llm_invalid": "The LLM validation returned an unreadable answer.",
        "llm_problem": "LLM validation: %s",
        "correction_failed": "The automatic correction failed (%s): the initial text is kept.",
    },
}

#############
# Functions #
#############


def messages(lang: str) -> dict:
    """
    Return the messages of a language.

    Parameters
    ----------
    lang : str
        Language code.

    Returns
    -------
    dict
        Messages.
    """
    return _MESSAGES.get(lang, _MESSAGES["en"])


def split_general_knowledge(text: str) -> Tuple[List[Tuple[str, bool]], bool]:
    """
    Split a text into sourced and general knowledge segments.

    Parameters
    ----------
    text : str
        Generated text with <cg> tags.

    Returns
    -------
    tuple
        List of (segment, is_general_knowledge) and whether the tags were
        balanced.
    """
    segments: List[Tuple[str, bool]] = []
    inside = False
    balanced = True
    for token in _CG_SPLIT.split(text):
        lowered = token.lower()
        if lowered == "<cg>":
            balanced = balanced and not inside
            inside = True
        elif lowered == "</cg>":
            balanced = balanced and inside
            inside = False
        elif token:
            segments.append((token, inside))
    return segments, balanced and not inside


def strip_urls(text: str) -> str:
    """
    Remove URLs from a text.

    Parameters
    ----------
    text : str
        Text.

    Returns
    -------
    str
        Text without URLs.
    """
    return _URL.sub("", text)


def cited_numbers(text: str) -> Set[int]:
    """
    Return the source numbers cited in a text.

    Parameters
    ----------
    text : str
        Text.

    Returns
    -------
    set of int
        Numbers, ranges like [1-3] being expanded.
    """
    numbers: Set[int] = set()
    for match in _CITATION.finditer(text):
        for part in re.split(r"\s*[,;]\s*", match.group(1)):
            bounds = re.split(r"\s*[–-]\s*", part)
            if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                start, end = int(bounds[0]), int(bounds[1])
                if 0 < end - start < 50:
                    numbers.update(range(start, end + 1))
                    continue
            numbers.update(int(b) for b in bounds if b.isdigit())
    return numbers


def check_locally(text: str, source_count: int, sources_text: str, allowed_years: Set[int],
                  rich: bool, truncated: bool, lang: str = "fr") -> ValidationReport:
    """
    Run the free local checks.

    Parameters
    ----------
    text : str
        Generated text.
    source_count : int
        Number of excerpts given to the LLM.
    sources_text : str
        Concatenation of the excerpts.
    allowed_years : set of int
        Years of the landmarks, allowed even if absent from the excerpts.
    rich : bool
        Whether general knowledge was forbidden.
    truncated : bool
        Whether the output limit was reached.
    lang : str
        Language of the messages.

    Returns
    -------
    ValidationReport
        Errors and warnings.
    """
    texts = messages(lang)
    report = ValidationReport()
    if not text.strip():
        report.errors.append(texts["empty"])
        return report
    if truncated:
        report.warnings.append(texts["truncated"])
    if _URL.search(text):
        report.warnings.append(texts["url"])

    segments, balanced = split_general_knowledge(text)
    if not balanced:
        report.warnings.append(texts["unbalanced"])
    general = [s for s, is_cg in segments if is_cg and s.strip()]
    sourced = " ".join(s for s, is_cg in segments if not is_cg)

    numbers = cited_numbers(text)
    unknown = sorted(n for n in numbers if n < 1 or n > source_count)
    if unknown:
        report.warnings.append(texts["unknown_ref"] % ", ".join("[%d]" % n for n in unknown))
    if general:
        report.warnings.append(texts["cg_count"] % len(general))

    source_years = {int(y) for y in _YEAR.findall(sources_text)} | set(allowed_years)
    missing = sorted({int(y) for y in _YEAR.findall(sourced)} - source_years)
    if missing:
        report.warnings.append(texts["years"] % ", ".join(str(y) for y in missing))
    return report


def parse_llm_validation(raw: str) -> Optional[Tuple[bool, List[str]]]:
    """
    Parse the JSON answer of the validation prompt.

    Parameters
    ----------
    raw : str
        Model answer, possibly wrapped in text or code fences.

    Returns
    -------
    tuple of (bool, list of str) or None
        Verdict and problems, None when the answer is unreadable.
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    problems = data.get("problemes", data.get("problems", []))
    if not isinstance(problems, list):
        problems = [str(problems)]
    problems = [str(p) for p in problems if str(p).strip()]
    ok = bool(data.get("ok", not problems)) and not problems
    return ok, problems
