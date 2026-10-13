"""
Plain data structures shared by the extraction, sources and LLM layers.

None of these classes depend on Gramps so that every layer except the
extraction and the note writing can be tested without it.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

###########
# Classes #
###########


@dataclass
class PlaceInfo:
    """
    Place of a landmark, split by administrative level.

    Attributes
    ----------
    name : str
        Most local meaningful name (usually the commune).
    department : str or None
        Department (or county) name.
    region : str or None
        Region, province or state name.
    country : str or None
        Country name.
    code : str or None
        Postal or INSEE code. Only used locally, never sent.
    latitude : float or None
        Latitude in decimal degrees. Only used for Wikipedia lookups.
    longitude : float or None
        Longitude in decimal degrees. Only used for Wikipedia lookups.
    """

    name: str
    department: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def key(self) -> Tuple[str, str, str]:
        """
        Return a key identifying the place for deduplication.

        Returns
        -------
        tuple of str
            Lower-cased name, department and country.
        """
        return (
            (self.name or "").lower(),
            (self.department or "").lower(),
            (self.country or "").lower(),
        )


@dataclass
class Landmark:
    """
    A year and a place, deliberately without any event label.

    Attributes
    ----------
    year : int or None
        Gregorian year.
    year_end : int or None
        End year for ranges.
    qualifier : str
        One of "", "about", "before", "after", "between".
    place : PlaceInfo or None
        Place of the landmark.
    """

    year: Optional[int] = None
    year_end: Optional[int] = None
    qualifier: str = ""
    place: Optional[PlaceInfo] = None

    def is_empty(self) -> bool:
        """
        Tell whether the landmark carries no usable information.

        Returns
        -------
        bool
            True when there is neither a year nor a place.
        """
        return self.year is None and self.place is None


@dataclass
class PersonFacts:
    """
    Minimal facts extracted from the tree for one person.

    Attributes
    ----------
    handle : str
        Gramps handle. Only used locally.
    gramps_id : str
        Gramps ID. Only used locally.
    name : str
        Display name. Only used locally (reports).
    landmarks : list of Landmark
        Birth (or baptism) and death (or burial) landmarks.
    occupation : str or None
        Occupation(s), only filled when the option is enabled.
    skip_reason : str or None
        Why the person cannot be processed ("living", "private",
        "no_landmarks"), or None.
    """

    handle: str
    gramps_id: str
    name: str
    landmarks: List[Landmark] = field(default_factory=list)
    occupation: Optional[str] = None
    skip_reason: Optional[str] = None


@dataclass
class ContextRequest:
    """
    Anonymized request: everything that may leave the computer.

    Attributes
    ----------
    lines : list of str
        Unlabeled landmark lines, sorted chronologically.
    occupation : str or None
        Occupation to put in context, if enabled.
    year_min : int or None
        Earliest year of the landmarks.
    year_max : int or None
        Latest year of the landmarks.
    places : list of PlaceInfo
        Places to look up on Wikipedia. Their names are sent to Wikipedia,
        their codes and coordinates are only used to resolve them.
    """

    lines: List[str]
    occupation: Optional[str] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    places: List[PlaceInfo] = field(default_factory=list)


@dataclass
class SourceExtract:
    """
    An excerpt of a Wikipedia article given to the LLM.

    Attributes
    ----------
    title : str
        Article title.
    level : str
        Geographic or historical level ("commune", "department",
        "province", "region", "country", "period").
    url : str
        Permanent link to the revision used.
    text : str
        Selected excerpt.
    number : int
        Reference number used in the generated text ([1], [2]...).
    """

    title: str
    level: str
    url: str
    text: str
    number: int = 0


@dataclass
class CollectedSources:
    """
    Result of the Wikipedia collection for one request.

    Attributes
    ----------
    extracts : list of SourceExtract
        Numbered excerpts.
    rich : bool
        Whether local sources (commune and department) are rich enough to
        forbid general knowledge.
    warnings : list of str
        Problems met during the collection.
    """

    extracts: List[SourceExtract] = field(default_factory=list)
    rich: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """
    Outcome of the local and optional LLM checks.

    Attributes
    ----------
    errors : list of str
        Blocking problems: the note is not written.
    warnings : list of str
        Non-blocking problems, shown in the report.
    llm_checked : bool
        Whether the optional LLM validation ran.
    llm_problems : int
        Number of problems reported by the LLM validation (after the
        automatic correction, when it ran).
    corrected_problems : list of str
        Problems fixed by the automatic correction.
    """

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    llm_checked: bool = False
    llm_problems: int = 0
    corrected_problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """
        Tell whether there is no blocking error.

        Returns
        -------
        bool
            True when no error was found.
        """
        return not self.errors


@dataclass
class GenerationResult:
    """
    Everything needed to write the note and the report.

    Attributes
    ----------
    text : str
        Generated context, possibly containing <cg> tags.
    sources : CollectedSources
        Sources given to the LLM.
    validation : ValidationReport
        Checks outcome.
    provider_label : str
        Human readable provider name.
    model : str
        Model used.
    from_cache : bool
        Whether the text came from the local cache.
    original_text : str or None
        Text before the automatic correction, None when it did not run.
    """

    text: str
    sources: CollectedSources
    validation: ValidationReport
    provider_label: str
    model: str
    from_cache: bool = False
    original_text: Optional[str] = None
