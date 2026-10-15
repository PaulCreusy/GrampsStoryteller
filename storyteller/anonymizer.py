"""
Turn the extracted facts into an anonymized request.

Only years, places and (optionally) the occupation are kept. Landmarks are
not labeled (no "birth" or "death") and are sorted chronologically.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import dataclasses
import re
from typing import List, Optional

# Local imports #
from . import fr_admin
from .models import ContextRequest, Landmark, PersonFacts, PlaceInfo

#############
# Constants #
#############

_WORDS = {
    "fr": {
        "about": "vers %d",
        "before": "avant %d",
        "after": "après %d",
        "between": "entre %d et %d",
        "unknown_date": "date inconnue",
        "unknown_place": "lieu inconnu",
    },
    "en": {
        "about": "around %d",
        "before": "before %d",
        "after": "after %d",
        "between": "between %d and %d",
        "unknown_date": "unknown date",
        "unknown_place": "unknown place",
    },
}

_STREET = re.compile(
    r"^(\d+\s*(bis|ter)?\b[\s,]*)?(rue|route|chemin|avenue|av\.|boulevard|bd|place|impasse|"
    r"all[ée]e|quai|cours|square|sentier|voie|faubourg|lieu-dit|lieudit|r[ée]sidence|"
    r"street|road|lane|drive)\b", re.IGNORECASE)

#############
# Functions #
#############


def commune_name(name: str) -> str:
    """
    Remove the street of a place entered as an address.

    "35 Rue Jules Guesde   Annoeullin" or "Rue de Don, Annoeullin" give
    "Annoeullin": the street would not be found on Wikipedia, and it is more
    identifying than the commune.

    Parameters
    ----------
    name : str
        Place name.

    Returns
    -------
    str
        Commune name.
    """
    parts = [part.strip() for part in re.split(r"\s{2,}|,", name) if part.strip()]
    if len(parts) > 1 and (_STREET.match(parts[0]) or parts[0][:1].isdigit()):
        return parts[1]
    return name


def complete_place(place: PlaceInfo) -> PlaceInfo:
    """
    Remove the street of an address and fill the department of a French
    place from its postal or INSEE code.

    Parameters
    ----------
    place : PlaceInfo
        Place from the tree.

    Returns
    -------
    PlaceInfo
        Same place with the department filled when it was missing.
    """
    name = commune_name(place.name)
    if name != place.name:
        place = dataclasses.replace(place, name=name)
    if place.department or (place.country and not fr_admin.is_france(place.country)):
        return place
    code = fr_admin.department_code_from_place_code(place.code)
    name = fr_admin.department_name(code)
    if not name:
        return place
    return PlaceInfo(
        name=place.name,
        department=name,
        region=place.region,
        country=place.country,
        code=place.code,
        latitude=place.latitude,
        longitude=place.longitude,
    )


def format_year(landmark: Landmark, lang: str = "fr") -> str:
    """
    Format the year of a landmark with its qualifier.

    Parameters
    ----------
    landmark : Landmark
        Landmark.
    lang : str
        "fr" or "en".

    Returns
    -------
    str
        Year text, e.g. "vers 1823".
    """
    words = _WORDS.get(lang, _WORDS["fr"])
    if landmark.year is None:
        return words["unknown_date"]
    if landmark.qualifier == "between" and landmark.year_end:
        return words["between"] % (landmark.year, landmark.year_end)
    if landmark.qualifier in ("about", "before", "after"):
        return words[landmark.qualifier] % landmark.year
    return str(landmark.year)


def format_place(place: Optional[PlaceInfo], lang: str = "fr") -> str:
    """
    Format a place as "Commune (Department, Region, Country)".

    Parameters
    ----------
    place : PlaceInfo or None
        Place.
    lang : str
        "fr" or "en".

    Returns
    -------
    str
        Place text. The code and the coordinates are never included.
    """
    words = _WORDS.get(lang, _WORDS["fr"])
    if place is None or not place.name:
        return words["unknown_place"]
    details = []
    for part in (place.department, place.region, place.country):
        if part and part not in details and part != place.name:
            details.append(part)
    if not details:
        return place.name
    return "%s (%s)" % (place.name, ", ".join(details))


def build_request(facts: PersonFacts, lang: str = "fr",
                  include_occupation: bool = False) -> ContextRequest:
    """
    Build the anonymized request of a person.

    Parameters
    ----------
    facts : PersonFacts
        Extracted facts.
    lang : str
        Language of the texts, "fr" or "en".
    include_occupation : bool
        Whether the occupation may be sent.

    Returns
    -------
    ContextRequest
        Request without name, identifier nor event label.
    """
    landmarks = [complete_landmark(lm) for lm in facts.landmarks if not lm.is_empty()]
    landmarks.sort(key=lambda lm: (lm.year is None, lm.year or 0))

    lines: List[str] = []
    places: List[PlaceInfo] = []
    seen_places = set()
    years: List[int] = []
    for landmark in landmarks:
        line = "%s – %s" % (format_year(landmark, lang), format_place(landmark.place, lang))
        if line not in lines:
            lines.append(line)
        if landmark.year is not None:
            years.append(landmark.year)
            if landmark.year_end:
                years.append(landmark.year_end)
        if landmark.place is not None and landmark.place.key() not in seen_places:
            seen_places.add(landmark.place.key())
            places.append(landmark.place)

    occupation = facts.occupation if include_occupation and facts.occupation else None
    return ContextRequest(
        lines=lines,
        occupation=occupation,
        year_min=min(years) if years else None,
        year_max=max(years) if years else None,
        places=places,
    )


def complete_landmark(landmark: Landmark) -> Landmark:
    """
    Return a copy of a landmark whose place department is completed.

    Parameters
    ----------
    landmark : Landmark
        Landmark.

    Returns
    -------
    Landmark
        Completed landmark.
    """
    if landmark.place is None:
        return landmark
    return Landmark(
        year=landmark.year,
        year_end=landmark.year_end,
        qualifier=landmark.qualifier,
        place=complete_place(landmark.place),
    )


def period_bounds(request: ContextRequest):
    """
    Compute the period of interest used to filter the sources.

    Parameters
    ----------
    request : ContextRequest
        Anonymized request.

    Returns
    -------
    tuple of (int or None, int or None)
        Lower and upper bounds. Both None when no year is known.
    """
    if request.year_min is None:
        return None, None
    if request.year_min == request.year_max:
        return request.year_min - 30, request.year_max + 30
    return request.year_min - 5, request.year_max
