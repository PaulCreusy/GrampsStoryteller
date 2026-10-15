"""
Build the text and the formatting of the note, independently of Gramps.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Local imports #
from .models import SourceExtract
from .prompts import LEVEL_NAMES
from .validation import cited_numbers, split_general_knowledge, strip_urls

#############
# Constants #
#############

# (kind, value, start, end) with kind in "bold", "italic", "link"
Tag = Tuple[str, Optional[str], int, int]

_TEXTS = {
    "fr": {
        "legend": "En italique : connaissances générales, non issues des sources ci-dessous.",
        "sources": "Sources",
        "footer": "Généré le %s — %s · %s",
        "date": "%d/%m/%Y",
    },
    "en": {
        "legend": "In italics: general knowledge, not taken from the sources below.",
        "sources": "Sources",
        "footer": "Generated on %s — %s · %s",
        "date": "%Y-%m-%d",
    },
}

###########
# Classes #
###########


@dataclass
class NoteContent:
    """
    Plain text of the note and its formatting ranges.

    Attributes
    ----------
    text : str
        Note text.
    tags : list of tuple
        Formatting as (kind, value, start, end).
    """

    text: str = ""
    tags: List[Tag] = field(default_factory=list)

    def append(self, text: str, kind: Optional[str] = None, value: Optional[str] = None):
        """
        Append text, optionally formatted.

        Parameters
        ----------
        text : str
            Text to append.
        kind : str, optional
            "bold", "italic" or "link".
        value : str, optional
            Link target.
        """
        start = len(self.text)
        self.text += text
        if kind and text:
            self.tags.append((kind, value, start, len(self.text)))


#############
# Functions #
#############


def _clean(text: str) -> str:
    """
    Remove URLs, markdown emphasis, extra spaces and extra blank lines.

    Parameters
    ----------
    text : str
        Generated text.

    Returns
    -------
    str
        Cleaned text.
    """
    text = strip_urls(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # Tags removed later must not leave double spaces or a space before a
    # punctuation mark ("briques <cg> et", "laitiers <cg>.")
    text = re.sub(r"[ \t]+(</?cg>)[ \t]+", r" \1", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+(</?cg>)(?=[.,;:!?])", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_note_content(text: str, sources: List[SourceExtract], provider_label: str, model: str,
                       lang: str = "fr", date: Optional[datetime.date] = None) -> NoteContent:
    """
    Build the note: context, sources and a discreet generation line.

    Parameters
    ----------
    text : str
        Generated text with <cg> tags.
    sources : list of SourceExtract
        Excerpts given to the LLM.
    provider_label : str
        Provider name.
    model : str
        Model name.
    lang : str
        Language code.
    date : datetime.date, optional
        Generation date, today by default.

    Returns
    -------
    NoteContent
        Text and formatting.
    """
    texts = _TEXTS.get(lang, _TEXTS["en"])
    levels = LEVEL_NAMES.get(lang, LEVEL_NAMES["en"])
    content = NoteContent()

    segments, _balanced = split_general_knowledge(_clean(text))
    has_general = False
    for segment, is_general in segments:
        if is_general and segment.strip():
            has_general = True
            stripped = segment.strip()
            leading = segment[:len(segment) - len(segment.lstrip())]
            trailing = segment[len(segment.rstrip()):]
            content.append(leading)
            content.append(stripped, "italic")
            content.append(trailing)
        else:
            content.append(segment)
    if has_general:
        content.append("\n\n")
        content.append(texts["legend"], "italic")

    cited = cited_numbers(text)
    listed = [s for s in sources if s.number in cited] or list(sources)
    if listed:
        content.append("\n\n")
        content.append(texts["sources"], "bold")
        for source in listed:
            content.append("\n[%d] %s (%s) — " % (source.number, source.title,
                                                  levels.get(source.level, source.level)))
            content.append(source.url, "link", source.url)

    day = (date or datetime.date.today()).strftime(texts["date"])
    content.append("\n\n")
    content.append(texts["footer"] % (day, provider_label, model), "italic")
    return content
