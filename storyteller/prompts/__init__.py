"""
Default prompts and prompt rendering.

Templates use the variables {reperes}, {profession}, {sources},
{consigne_sources}, {langue}, {longueur} and, for the validation and
correction prompts, {texte} and {problemes}. Only these names are replaced, so other braces (JSON examples) are
left untouched.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

# Local imports #
from ..models import ContextRequest, SourceExtract

#############
# Constants #
#############

PROMPT_DIR = os.path.dirname(os.path.abspath(__file__))
VARIABLES = ("reperes", "profession", "sources", "consigne_sources", "langue", "longueur", "texte",
             "problemes")
KINDS = ("system", "user", "validation", "correction")

LANGUAGE_NAMES = {
    "fr": {"fr": "français", "en": "anglais"},
    "en": {"fr": "French", "en": "English"},
}

LEVEL_NAMES = {
    "fr": {
        "commune": "commune",
        "department": "département",
        "province": "province historique",
        "region": "région",
        "country": "pays",
        "period": "période historique nationale",
    },
    "en": {
        "commune": "town",
        "department": "department",
        "province": "historical province",
        "region": "region",
        "country": "country",
        "period": "national historical period",
    },
}

_TEXTS = {
    "fr": {
        "occupation": "Métier courant dans ces lieux, à décrire dans le paragraphe sur la vie "
                      "quotidienne (sans l'attribuer à quelqu'un) : %s",
        "rich": "Les extraits sont riches : les paragraphes 1 et 2 reposent uniquement sur eux. "
                "Les connaissances générales, balisées <cg>, ne sont admises que pour la vie "
                "quotidienne (paragraphe 3).",
        "poor": "Les extraits sont limités : tu peux les compléter par des connaissances "
                "historiques générales bien établies, réduites au strict nécessaire et balisées "
                "<cg>. Privilégie toujours les extraits quand ils couvrent le sujet.",
        "no_source": "(aucun extrait disponible)",
    },
    "en": {
        "occupation": "Common occupation in these places, to describe in the daily life "
                      "paragraph (without attributing it to anyone): %s",
        "rich": "The excerpts are rich: paragraphs 1 and 2 rely on them only. General knowledge, "
                "wrapped in <cg>, is only allowed for daily life (paragraph 3).",
        "poor": "The excerpts are limited: you may complete them with well-established general "
                "historical knowledge, kept to the strict minimum and wrapped in <cg>. Always "
                "prefer the excerpts when they cover the subject.",
        "no_source": "(no excerpt available)",
    },
}

#############
# Functions #
#############


def _lang(lang: str) -> str:
    """
    Return a supported language code.

    Parameters
    ----------
    lang : str
        Requested language.

    Returns
    -------
    str
        "fr" or "en".
    """
    return lang if lang in _TEXTS else "en"


def default_prompt(kind: str, lang: str) -> str:
    """
    Read a default prompt.

    Parameters
    ----------
    kind : str
        "system", "user", "validation" or "correction".
    lang : str
        Language code.

    Returns
    -------
    str
        Template text.
    """
    path = os.path.join(PROMPT_DIR, "%s_%s.txt" % (kind, _lang(lang)))
    with open(path, encoding="utf-8") as file:
        return file.read().strip() + "\n"


def render(template: str, variables: Dict[str, str]) -> str:
    """
    Replace the known {variables} of a template.

    Parameters
    ----------
    template : str
        Template.
    variables : dict
        Values by variable name.

    Returns
    -------
    str
        Rendered text.
    """
    text = template
    for name in VARIABLES:
        if name in variables:
            text = text.replace("{%s}" % name, variables[name])
    # An empty variable (e.g. no occupation) must not leave blank lines
    return re.sub(r"\n{3,}", "\n\n", text)


def format_sources(extracts: List[SourceExtract], lang: str) -> str:
    """
    Format the numbered excerpts for the prompt.

    Parameters
    ----------
    extracts : list of SourceExtract
        Excerpts.
    lang : str
        Language code.

    Returns
    -------
    str
        Excerpts block.
    """
    lang = _lang(lang)
    if not extracts:
        return _TEXTS[lang]["no_source"]
    blocks = []
    for extract in extracts:
        level = LEVEL_NAMES[lang].get(extract.level, extract.level)
        blocks.append("[%d] %s (%s)\n\"\"\"\n%s\n\"\"\"" % (
            extract.number, extract.title, level, extract.text))
    return "\n\n".join(blocks)


def build_variables(request: ContextRequest, extracts: List[SourceExtract], rich: bool,
                    lang: str, length_words: int) -> Dict[str, str]:
    """
    Compute the template variables of a request.

    Parameters
    ----------
    request : ContextRequest
        Anonymized request.
    extracts : list of SourceExtract
        Numbered excerpts.
    rich : bool
        Whether general knowledge is forbidden.
    lang : str
        Language code.
    length_words : int
        Target length.

    Returns
    -------
    dict
        Variables.
    """
    lang = _lang(lang)
    texts = _TEXTS[lang]
    return {
        "reperes": "\n".join("- %s" % line for line in request.lines),
        "profession": texts["occupation"] % request.occupation if request.occupation else "",
        "sources": format_sources(extracts, lang),
        "consigne_sources": texts["rich"] if rich else texts["poor"],
        "langue": LANGUAGE_NAMES[lang][lang],
        "longueur": str(length_words),
    }


def template_or_default(custom: Optional[str], kind: str, lang: str) -> str:
    """
    Return the user template if set, otherwise the default one.

    Parameters
    ----------
    custom : str or None
        Template from the settings.
    kind : str
        "system", "user", "validation" or "correction".
    lang : str
        Language code.

    Returns
    -------
    str
        Template.
    """
    return custom if custom and custom.strip() else default_prompt(kind, lang)
