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
from .llm.base import strip_reasoning
from .models import ValidationReport

#############
# Constants #
#############

_CITATION = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
_CG_SPLIT = re.compile(r"(</?cg>)", re.IGNORECASE)
_YEAR = re.compile(r"(?<![\d.,])(1[0-9]{3}|20[0-9]{2})(?!\d)")
_URL = re.compile(r"https?://\S+")
_QUOTE_CHARS = "\"'«»‘’“”"
_QUOTED = re.compile(r"«\s*(.+?)\s*»|‘(.+?)’|“(.+?)”")
# Reasons meaning "not in the excerpts", which do not apply to general knowledge
_ABSENT = re.compile(r"absent|non contenu|pas contenu|ne figure|pas dans les extraits|non sourc|"
                     r"pas sourc|n'apparai|not in the excerpts|not contained|not supported|"
                     r"unsupported|not sourced|not mentioned", re.IGNORECASE)
_WRONG = re.compile(r"faux|fausse|erron|inexact|anachron|contredi|wrong|false|incorrect|"
                    r"inaccurate|contradict", re.IGNORECASE)
_LOOSE_OK = re.compile(r'"?ok"?\s*:\s*"?(true|false)', re.IGNORECASE)
_LOOSE_PROBLEMS = re.compile(r'"?(?:problemes|problèmes|problems)"?\s*:\s*\[', re.IGNORECASE)

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
        "llm_invalid": "La validation par LLM a renvoyé une réponse illisible : « %s »",
        "llm_truncated": "La réponse de la validation par LLM a été coupée par la limite de "
                         "tokens : augmentez « Tokens max ».",
        "llm_problem": "Validation LLM : %s",
        "correction_failed": "La correction automatique a échoué (%s) : le texte initial est "
                             "conservé.",
        "correction_tags": "le marquage des connaissances générales a été perdu",
    },
    "en": {
        "empty": "The generated text is empty.",
        "truncated": "The text was cut by the token limit: increase \"Max tokens\".",
        "unknown_ref": "References to missing sources: %s.",
        "unbalanced": "Unbalanced <cg> tags: the general knowledge marking is approximate.",
        "cg_count": "%d general knowledge passage(s) (unsourced), shown in italics in the note.",
        "years": "Years cited as sourced but missing from the excerpts: %s.",
        "url": "The text contained URLs, they were removed.",
        "llm_invalid": "The LLM validation returned an unreadable answer: \"%s\"",
        "llm_truncated": "The answer of the LLM validation was cut by the token limit: "
                         "increase \"Max tokens\".",
        "llm_problem": "LLM validation: %s",
        "correction_failed": "The automatic correction failed (%s): the initial text is kept.",
        "correction_tags": "the general knowledge marking was lost",
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


def repair_cg_tags(text: str) -> str:
    """
    Complete the <cg> tags cut by the model (e.g. a final "</cg").

    Parameters
    ----------
    text : str
        Generated text.

    Returns
    -------
    str
        Text whose tags all end with ">".
    """
    return re.sub(r"<(/?)cg\b(?!>)\s*", r"<\1cg>", text, flags=re.IGNORECASE)


def tags_balanced(text: str) -> bool:
    """
    Tell whether the <cg> tags of a text are balanced.

    Parameters
    ----------
    text : str
        Generated text.

    Returns
    -------
    bool
        True when every <cg> is closed.
    """
    return split_general_knowledge(text)[1]


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


def _verdict(data: dict) -> Optional[Tuple[bool, List[str]]]:
    """
    Read the verdict and the problems of a decoded validation answer.

    Parameters
    ----------
    data : dict
        Decoded JSON object.

    Returns
    -------
    tuple of (bool, list of str) or None
        Verdict and problems, None when the object is not a verdict.
    """
    if not any(key in data for key in ("ok", "problemes", "problèmes", "problems")):
        return None
    problems = data.get("problemes", data.get("problèmes", data.get("problems", [])))
    if not isinstance(problems, list):
        problems = [problems] if problems else []
    problems = [str(p).strip() for p in problems if str(p).strip()]
    ok = data.get("ok", not problems)
    if isinstance(ok, str):
        ok = ok.strip().lower() in ("true", "yes", "oui")
    return bool(ok) and not problems, problems


def _decoded_objects(text: str) -> List[dict]:
    """
    Decode every JSON object found in a text.

    Parameters
    ----------
    text : str
        Text containing JSON objects among other words.

    Returns
    -------
    list of dict
        Decoded objects, in order.
    """
    decoder = json.JSONDecoder()
    objects = []
    position = text.find("{")
    while position >= 0:
        try:
            value, end = decoder.raw_decode(text, position)
        except ValueError:
            position = text.find("{", position + 1)
            continue
        if isinstance(value, dict):
            objects.append(value)
        position = text.find("{", end)
    return objects


def _loose_verdict(text: str) -> Optional[Tuple[bool, List[str]]]:
    """
    Read a verdict from an almost-JSON answer, e.g. with unescaped quotes
    inside the problems.

    Parameters
    ----------
    text : str
        Answer.

    Returns
    -------
    tuple of (bool, list of str) or None
        Verdict and problems, None when nothing looks like a verdict.
    """
    ok_match = _LOOSE_OK.search(text)
    list_match = _LOOSE_PROBLEMS.search(text)
    if list_match is None:
        if ok_match is None:
            return None
        ok = ok_match.group(1).lower() == "true"
        return (True, []) if ok else (False, [])
    body = text[list_match.end():]
    end = body.rfind("]")
    if end < 0:
        return None
    body = body[:end].strip().rstrip(",").strip()
    # Opening quote of the first item and closing quote of the last one
    if body.startswith('"'):
        body = body[1:]
    if body.endswith('"'):
        body = body[:-1]
    problems = []
    # Items are separated by a closing quote, a comma and an opening quote
    for item in re.split(r'"\s*,\s*"', body) if body.strip() else []:
        item = item.strip().replace('\\"', '"')
        if item:
            problems.append(item)
    ok = ok_match.group(1).lower() == "true" if ok_match else not problems
    return ok and not problems, problems


def parse_llm_validation(raw: str) -> Optional[Tuple[bool, List[str]]]:
    """
    Parse the JSON answer of the validation prompt.

    Small local models often wrap the JSON in reasoning or code fences, use
    typographic quotes, leave trailing commas or quote passages with
    unescaped double quotes: all these answers are accepted.

    Parameters
    ----------
    raw : str
        Model answer.

    Returns
    -------
    tuple of (bool, list of str) or None
        Verdict and problems, None when the answer is unreadable.
    """
    text = strip_reasoning(raw)
    for candidate in (text, _repair_json(text)):
        verdicts = [v for v in (_verdict(o) for o in _decoded_objects(candidate)) if v]
        if verdicts:
            return verdicts[-1]
    return _loose_verdict(_repair_json(text))


def _repair_json(text: str) -> str:
    """
    Fix the most common JSON mistakes of small models.

    Parameters
    ----------
    text : str
        Answer.

    Returns
    -------
    str
        Answer with straight quotes and without trailing commas.
    """
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return re.sub(r"\b(True|False)\b", lambda m: m.group(1).lower(), text)


def answer_excerpt(raw: str, length: int = 160) -> str:
    """
    Return the beginning of an answer on one line, for an error message.

    Parameters
    ----------
    raw : str
        Answer.
    length : int
        Maximum number of characters.

    Returns
    -------
    str
        Excerpt.
    """
    text = " ".join(raw.split())
    return text if len(text) <= length else text[:length] + "…"


def _split_problem(problem: str) -> Tuple[str, str]:
    """
    Split a validation problem into the quoted passage and the explanation.

    Parameters
    ----------
    problem : str
        Problem, usually « passage » : explanation.

    Returns
    -------
    tuple of (str, str)
        Passage (the part before the first colon when nothing is quoted)
        and explanation.
    """
    match = _QUOTED.search(problem)
    if match and match.start() <= 2:
        passage = next(group for group in match.groups() if group)
        reason = problem[match.end():]
    else:
        parts = re.split(r"\s*:\s", problem, maxsplit=1)
        passage, reason = parts[0], parts[1] if len(parts) > 1 else ""
    # Stray quotes of an almost-JSON answer
    passage = passage.strip(_QUOTE_CHARS + " ")
    reason = reason.strip().lstrip(":").strip()
    return passage, reason


def tidy_problem(problem: str, lang: str = "fr") -> str:
    """
    Rewrite a validation problem as « passage » : explanation.

    Parameters
    ----------
    problem : str
        Problem as written by the model.
    lang : str
        Language of the quotes.

    Returns
    -------
    str
        Problem with balanced quotes.
    """
    passage, reason = _split_problem(problem)
    if not reason:
        return problem.strip()
    if lang == "fr":
        return "« %s » : %s" % (passage, reason)
    return "‘%s’: %s" % (passage, reason)


def drop_unfounded_cg_problems(text: str, problems: List[str]) -> List[str]:
    """
    Remove the problems that only blame general knowledge for not being in
    the excerpts: it is allowed when marked with <cg>.

    Parameters
    ----------
    text : str
        Checked text, with <cg> tags.
    problems : list of str
        Problems found by the LLM validation.

    Returns
    -------
    list of str
        Problems to keep.
    """
    segments, _balanced = split_general_knowledge(text)

    def flat(value: str) -> str:
        return " ".join(value.replace("…", " ").replace("...", " ").split()).lower()

    general = flat(" ".join(s for s, is_cg in segments if is_cg))
    sourced = flat(" ".join(s for s, is_cg in segments if not is_cg))
    kept = []
    for problem in problems:
        passage, reason = _split_problem(problem)
        passage = flat(passage)
        in_general = len(passage) >= 12 and passage in general and passage not in sourced
        if in_general and _ABSENT.search(reason) and not _WRONG.search(reason):
            continue
        kept.append(problem)
    return kept
