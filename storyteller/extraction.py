"""
Extraction of the minimal facts of a person from a Gramps database.

Only the birth (or baptism) and the death (or burial) years and places are
read, plus the occupation. Living persons, private persons and private
events or places are excluded.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import List, Optional, Tuple

# Dependencies #
from gramps.gen.lib import AttributeType, Date, EventRoleType, EventType, PlaceType
from gramps.gen.utils.alive import probably_alive
from gramps.gen.utils.place import conv_lat_lon

# Local imports #
from .models import Landmark, PersonFacts, PlaceInfo

#############
# Constants #
#############

SKIP_LIVING = "living"
SKIP_PRIVATE = "private"
SKIP_NO_LANDMARK = "no_landmarks"

BIRTH_FALLBACKS = (EventType.BAPTISM, EventType.CHRISTEN)
DEATH_FALLBACKS = (EventType.BURIAL, EventType.CREMATION)

COMMUNE_TYPES = (
    PlaceType.CITY, PlaceType.TOWN, PlaceType.VILLAGE, PlaceType.MUNICIPALITY,
    PlaceType.PARISH, PlaceType.HAMLET, PlaceType.LOCALITY, PlaceType.BOROUGH,
)
DEPARTMENT_TYPES = (PlaceType.DEPARTMENT, PlaceType.COUNTY)
REGION_TYPES = (PlaceType.REGION, PlaceType.PROVINCE, PlaceType.STATE)
UPPER_TYPES = DEPARTMENT_TYPES + REGION_TYPES + (PlaceType.COUNTRY,)

MAX_HIERARCHY_DEPTH = 12

#############
# Functions #
#############


def _date_landmark(date: Date) -> Tuple[Optional[int], Optional[int], str]:
    """
    Convert a Gramps date to a Gregorian year with a qualifier.

    Parameters
    ----------
    date : Date
        Gramps date (any calendar).

    Returns
    -------
    tuple
        (year, end year, qualifier). Year is None for empty or text-only
        dates.
    """
    if date is None or date.is_empty() or date.get_modifier() == Date.MOD_TEXTONLY:
        return None, None, ""
    if date.get_calendar() != Date.CAL_GREGORIAN:
        try:
            date = date.to_calendar("gregorian")
        except (AttributeError, ValueError):
            pass
    year = date.get_year() or None
    if year is None:
        return None, None, ""

    modifier = date.get_modifier()
    qualifier = ""
    year_end = None
    if modifier == Date.MOD_ABOUT:
        qualifier = "about"
    elif modifier in (Date.MOD_BEFORE, getattr(Date, "MOD_TO", -1)):
        qualifier = "before"
    elif modifier in (Date.MOD_AFTER, getattr(Date, "MOD_FROM", -1)):
        qualifier = "after"
    elif modifier in (Date.MOD_RANGE, Date.MOD_SPAN):
        year_end = date.get_stop_year() or None
        if year_end and year_end != year:
            qualifier = "between"
        else:
            year_end = None
    if not qualifier and date.get_quality() in (Date.QUAL_ESTIMATED, Date.QUAL_CALCULATED):
        qualifier = "about"
    return year, year_end, qualifier


def _hierarchy(db, place) -> List[object]:
    """
    Return a place and its enclosing places, from the most local one.

    Parameters
    ----------
    db : DbReadBase
        Database.
    place : Place
        Starting place.

    Returns
    -------
    list of Place
        Places, without loops.
    """
    chain = [place]
    seen = {place.get_handle()}
    current = place
    while len(chain) < MAX_HIERARCHY_DEPTH:
        refs = current.get_placeref_list()
        if not refs:
            break
        parent = db.get_place_from_handle(refs[0].ref)
        if parent is None or parent.get_handle() in seen:
            break
        seen.add(parent.get_handle())
        chain.append(parent)
        current = parent
    return chain


def _coordinates(place) -> Tuple[Optional[float], Optional[float]]:
    """
    Read the coordinates of a place in decimal degrees.

    Parameters
    ----------
    place : Place
        Place.

    Returns
    -------
    tuple of (float or None, float or None)
        Latitude and longitude.
    """
    latitude, longitude = place.get_latitude(), place.get_longitude()
    if not latitude or not longitude:
        return None, None
    converted = conv_lat_lon(latitude, longitude, "D.D8")
    try:
        return float(converted[0]), float(converted[1])
    except (TypeError, ValueError, IndexError):
        return None, None


def place_info(db, place) -> Optional[PlaceInfo]:
    """
    Split a place hierarchy into commune, department, region and country.

    Parameters
    ----------
    db : DbReadBase
        Database.
    place : Place
        Place of the event.

    Returns
    -------
    PlaceInfo or None
        Place information, None when the place is private or nameless.
    """
    if place is None or place.get_privacy():
        return None
    chain = [p for p in _hierarchy(db, place) if not p.get_privacy()]
    if not chain:
        return None
    typed = [(int(p.get_type()), p.get_name().get_value().strip(), p) for p in chain]

    def first(types):
        for place_type, name, obj in typed:
            if place_type in types and name:
                return name, obj
        return None, None

    commune, commune_obj = first(COMMUNE_TYPES)
    if commune is None:
        for place_type, name, obj in typed:
            if place_type not in UPPER_TYPES and name:
                commune, commune_obj = name, obj
                break
    department, _ = first(DEPARTMENT_TYPES)
    region, _ = first(REGION_TYPES)
    country, _ = first((PlaceType.COUNTRY,))

    # A single place named "Brest, Finistère, Bretagne, France"
    if len(chain) == 1 and commune and "," in commune and not (department or country):
        parts = [part.strip() for part in commune.split(",") if part.strip()]
        commune = parts[0]
        if len(parts) >= 2:
            country = parts[-1]
        if len(parts) >= 3:
            department = parts[1]
        if len(parts) >= 4:
            region = parts[2]
    if not commune:
        commune = department or region or country
    if not commune:
        return None

    start = chain.index(commune_obj) if commune_obj in chain else 0
    code = next((p.get_code().strip() for p in chain[start:] if p.get_code().strip()), None)
    latitude, longitude = _coordinates(commune_obj or place)
    return PlaceInfo(name=commune, department=department, region=region, country=country,
                     code=code, latitude=latitude, longitude=longitude)


def _usable_event(db, event_ref):
    """
    Return the event of a reference if the person is its principal and
    nothing is private.

    Parameters
    ----------
    db : DbReadBase
        Database.
    event_ref : EventRef
        Reference.

    Returns
    -------
    Event or None
        Event, None when unusable.
    """
    if event_ref is None or event_ref.get_privacy():
        return None
    if event_ref.get_role() != EventRoleType.PRIMARY:
        return None
    event = db.get_event_from_handle(event_ref.ref)
    if event is None or event.get_privacy():
        return None
    return event


def _landmark(db, event) -> Optional[Landmark]:
    """
    Build the landmark of an event.

    Parameters
    ----------
    db : DbReadBase
        Database.
    event : Event
        Event.

    Returns
    -------
    Landmark or None
        Landmark, None when the event has neither year nor place.
    """
    year, year_end, qualifier = _date_landmark(event.get_date_object())
    place = None
    if event.get_place_handle():
        place = place_info(db, db.get_place_from_handle(event.get_place_handle()))
    landmark = Landmark(year=year, year_end=year_end, qualifier=qualifier, place=place)
    return None if landmark.is_empty() else landmark


def _find_landmark(db, person, main_ref, fallbacks) -> Optional[Landmark]:
    """
    Return the landmark of the main event, or of the first usable fallback.

    Parameters
    ----------
    db : DbReadBase
        Database.
    person : Person
        Person.
    main_ref : EventRef or None
        Birth or death reference.
    fallbacks : tuple of int
        Fallback event types.

    Returns
    -------
    Landmark or None
        Landmark.
    """
    event = _usable_event(db, main_ref)
    if event is not None:
        landmark = _landmark(db, event)
        if landmark is not None:
            return landmark
    for event_ref in person.get_event_ref_list():
        event = _usable_event(db, event_ref)
        if event is not None and int(event.get_type()) in fallbacks:
            landmark = _landmark(db, event)
            if landmark is not None:
                return landmark
    return None


def occupation(db, person) -> Optional[str]:
    """
    Return the occupations of a person, as entered in the tree.

    Parameters
    ----------
    db : DbReadBase
        Database.
    person : Person
        Person.

    Returns
    -------
    str or None
        Occupations separated by "; ", None when unknown.
    """
    values: List[str] = []
    for event_ref in person.get_event_ref_list():
        event = _usable_event(db, event_ref)
        if event is not None and int(event.get_type()) == EventType.OCCUPATION:
            values.append(event.get_description().strip())
    for attribute in person.get_attribute_list():
        if not attribute.get_privacy() and int(attribute.get_type()) == AttributeType.OCCUPATION:
            values.append(attribute.get_value().strip())
    unique = []
    for value in values:
        if value and value.lower() not in (v.lower() for v in unique):
            unique.append(value)
    return "; ".join(unique) or None


def extract_person(db, person, name: str = "", include_occupation: bool = False) -> PersonFacts:
    """
    Extract the facts of a person.

    Parameters
    ----------
    db : DbReadBase
        Database.
    person : Person
        Person.
    name : str
        Display name, only used locally in reports.
    include_occupation : bool
        Whether to read the occupation.

    Returns
    -------
    PersonFacts
        Facts, with skip_reason set when the person must not be processed.
    """
    facts = PersonFacts(handle=person.get_handle(), gramps_id=person.get_gramps_id(), name=name)
    if person.get_privacy():
        facts.skip_reason = SKIP_PRIVATE
        return facts
    if probably_alive(person, db):
        facts.skip_reason = SKIP_LIVING
        return facts

    for main_ref, fallbacks in ((person.get_birth_ref(), BIRTH_FALLBACKS),
                                (person.get_death_ref(), DEATH_FALLBACKS)):
        landmark = _find_landmark(db, person, main_ref, fallbacks)
        if landmark is not None:
            facts.landmarks.append(landmark)
    if not facts.landmarks:
        facts.skip_reason = SKIP_NO_LANDMARK
        return facts
    if include_occupation:
        facts.occupation = occupation(db, person)
    return facts
