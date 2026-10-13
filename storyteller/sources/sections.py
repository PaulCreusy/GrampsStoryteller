"""
Split Wikipedia plain-text extracts into sections and keep the paragraphs
relevant to a period.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Local imports #
from ..fr_admin import normalize

#############
# Constants #
#############

Span = Tuple[int, int]

_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1$")
_YEAR = re.compile(r"(?<![\d.,])(1[0-9]{3}|20[0-9]{2})(?!\d)")
_ROMAN_CENTURY = re.compile(r"\b([IVXL]{1,6})(?:e|ᵉ|er|ème|eme)\b")
_EN_CENTURY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[\s-]+centur", re.IGNORECASE)
_EN_DECADE = re.compile(r"\b(1[0-9]{2}|20[0-9])0s\b")

_HISTORY_TITLES = ("histoire", "historique", "history")

# Era keywords found in section headings, most specific first
_ERAS: List[Tuple[str, Span]] = [
    ("prehistoire", (-10000, -800)),
    ("prehistory", (-10000, -800)),
    ("haut moyen age", (476, 1000)),
    ("bas moyen age", (1000, 1492)),
    ("moyen age", (476, 1492)),
    ("medieval", (476, 1492)),
    ("middle ages", (476, 1492)),
    ("antiquite", (-800, 476)),
    ("antiquity", (-800, 476)),
    ("gallo romain", (-50, 476)),
    ("renaissance", (1450, 1600)),
    ("ancien regime", (1492, 1789)),
    ("epoque moderne", (1492, 1789)),
    ("temps modernes", (1492, 1789)),
    ("early modern", (1492, 1789)),
    ("revolution industrielle", (1780, 1900)),
    ("industrial revolution", (1760, 1900)),
    ("revolution", (1789, 1799)),
    ("consulat", (1799, 1804)),
    ("premier empire", (1804, 1815)),
    ("napoleon", (1799, 1815)),
    ("napoleonic", (1799, 1815)),
    ("restauration", (1814, 1830)),
    ("restoration", (1814, 1830)),
    ("monarchie de juillet", (1830, 1848)),
    ("july monarchy", (1830, 1848)),
    ("second empire", (1852, 1870)),
    ("guerre franco", (1870, 1871)),
    ("franco prussian", (1870, 1871)),
    ("premiere guerre mondiale", (1914, 1918)),
    ("grande guerre", (1914, 1918)),
    ("first world war", (1914, 1918)),
    ("world war i", (1914, 1918)),
    ("entre deux guerres", (1918, 1939)),
    ("interwar", (1918, 1939)),
    ("seconde guerre mondiale", (1939, 1945)),
    ("deuxieme guerre mondiale", (1939, 1945)),
    ("second world war", (1939, 1945)),
    ("world war ii", (1939, 1945)),
    ("occupation", (1940, 1944)),
    ("trente glorieuses", (1945, 1975)),
    ("apres guerre", (1945, 1960)),
    ("post war", (1945, 1960)),
    ("epoque contemporaine", (1789, 2100)),
    ("periode contemporaine", (1789, 2100)),
    ("contemporary", (1789, 2100)),
    ("modern", (1789, 2100)),
]

###########
# Classes #
###########


@dataclass
class Section:
    """
    Section of an article.

    Attributes
    ----------
    title : str
        Heading text, empty for the lead section.
    level : int
        Heading level (1 for the lead, 2 for "==", ...).
    paragraphs : list of str
        Non-empty paragraphs.
    """

    title: str
    level: int
    paragraphs: List[str] = field(default_factory=list)


#############
# Functions #
#############


def parse_sections(text: str) -> List[Section]:
    """
    Split a plain-text extract (exsectionformat=wiki) into sections.

    Parameters
    ----------
    text : str
        Extract.

    Returns
    -------
    list of Section
        Sections, the first one being the lead.
    """
    sections = [Section(title="", level=1)]
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _HEADING.match(line)
        if match:
            sections.append(Section(title=match.group(2), level=len(match.group(1))))
        else:
            sections[-1].paragraphs.append(line)
    return sections


def history_sections(sections: List[Section]) -> List[Section]:
    """
    Return the history section(s) and their subsections.

    Parameters
    ----------
    sections : list of Section
        Article sections.

    Returns
    -------
    list of Section
        History sections, empty when the article has none.
    """
    selected: List[Section] = []
    history_level: Optional[int] = None
    for section in sections[1:]:
        if history_level is not None and section.level > history_level:
            selected.append(section)
            continue
        history_level = None
        if normalize(section.title).startswith(_HISTORY_TITLES):
            history_level = section.level
            selected.append(section)
    return selected


def _roman_to_int(roman: str) -> int:
    """
    Convert a roman numeral to an integer.

    Parameters
    ----------
    roman : str
        Roman numeral in upper case.

    Returns
    -------
    int
        Value.
    """
    values = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    for index, char in enumerate(roman):
        value = values[char]
        if index + 1 < len(roman) and values[roman[index + 1]] > value:
            total -= value
        else:
            total += value
    return total


def time_spans(text: str) -> List[Span]:
    """
    Find the years, decades and centuries mentioned in a text.

    Parameters
    ----------
    text : str
        Paragraph or heading.

    Returns
    -------
    list of tuple of int
        Spans (first year, last year).
    """
    spans: List[Span] = [(int(y), int(y)) for y in _YEAR.findall(text)]
    for match in _ROMAN_CENTURY.finditer(text):
        if "siècle" in text[match.end():match.end() + 30]:
            century = _roman_to_int(match.group(1))
            spans.append(((century - 1) * 100 + 1, century * 100))
    for match in _EN_CENTURY.finditer(text):
        century = int(match.group(1))
        spans.append(((century - 1) * 100 + 1, century * 100))
    for match in _EN_DECADE.finditer(text):
        start = int(match.group(1)) * 10
        spans.append((start, start + (99 if start % 100 == 0 else 9)))
    return spans


def era_spans(heading: str) -> List[Span]:
    """
    Find the period covered by a section heading.

    Parameters
    ----------
    heading : str
        Section title.

    Returns
    -------
    list of tuple of int
        Spans, empty when the heading gives no period.
    """
    spans = time_spans(heading)
    normalized = " %s " % normalize(heading)
    for keyword, span in _ERAS:
        if " %s " % keyword in normalized:
            spans.append(span)
            break
    return spans


def overlaps(spans: List[Span], low: int, high: int) -> bool:
    """
    Tell whether one of the spans intersects [low, high].

    Parameters
    ----------
    spans : list of tuple of int
        Spans.
    low : int
        Lower bound.
    high : int
        Upper bound.

    Returns
    -------
    bool
        True when at least one span intersects.
    """
    return any(start <= high and end >= low for start, end in spans)


def _fit(paragraph: str, budget: int) -> str:
    """
    Cut a paragraph at a sentence end so that it fits the budget.

    Parameters
    ----------
    paragraph : str
        Paragraph.
    budget : int
        Maximum length.

    Returns
    -------
    str
        Cut paragraph, empty when no sentence fits.
    """
    if len(paragraph) <= budget:
        return paragraph
    cut = paragraph[:budget]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 0 else ""


def select_paragraphs(sections: List[Section], low: Optional[int], high: Optional[int],
                      budget: int) -> str:
    """
    Keep the paragraphs of the sections relevant to [low, high].

    A paragraph mentioning dates is kept when one of them intersects the
    period. A paragraph without date is kept when its heading covers the
    period, or when it directly follows a kept dated paragraph in a section
    whose heading gives no period (undated paragraphs do not chain).

    Parameters
    ----------
    sections : list of Section
        Sections to filter (usually the history sections).
    low : int or None
        Lower bound of the period. None keeps every paragraph.
    high : int or None
        Upper bound of the period.
    budget : int
        Maximum number of characters.

    Returns
    -------
    str
        Selected paragraphs, with subsection titles.
    """
    output: List[str] = []
    used = 0
    for section in sections:
        heading = era_spans(section.title)
        heading_known = bool(heading)
        heading_relevant = low is None or (heading_known and overlaps(heading, low, high))
        previous_dated_kept = False
        title_written = False
        for paragraph in section.paragraphs:
            if low is None:
                keep = True
            else:
                spans = time_spans(paragraph)
                if spans:
                    keep = overlaps(spans, low, high)
                elif heading_known:
                    keep = heading_relevant
                else:
                    keep = previous_dated_kept
                previous_dated_kept = bool(spans) and keep
            if not keep:
                continue
            title = "— %s —" % section.title if section.title and not title_written else ""
            remaining = budget - used - len(title)
            piece = _fit(paragraph, remaining)
            if not piece:
                continue
            if title:
                output.append(title)
                used += len(title)
                title_written = True
            output.append(piece)
            used += len(piece)
    return "\n".join(output)


def lead_text(sections: List[Section], budget: int) -> str:
    """
    Return the beginning of the lead section.

    Parameters
    ----------
    sections : list of Section
        Article sections.
    budget : int
        Maximum number of characters.

    Returns
    -------
    str
        Lead paragraphs within the budget.
    """
    if not sections:
        return ""
    return select_paragraphs([sections[0]], None, None, budget)
