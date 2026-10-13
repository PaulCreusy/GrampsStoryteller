"""
Tests of the anonymized request.
"""

###########
# Imports #
###########

# Local imports #
from storyteller.anonymizer import build_request, format_year, period_bounds
from storyteller.models import ContextRequest, Landmark, PersonFacts, PlaceInfo

#############
# Functions #
#############


def _facts(**kwargs):
    defaults = dict(handle="H123", gramps_id="I0042", name="Jean Le Gall")
    defaults.update(kwargs)
    return PersonFacts(**defaults)


def test_request_contains_no_identity_and_no_event_label(brest):
    death = Landmark(year=1891, place=PlaceInfo(name="Brest", department="Finistère",
                                                country="France"))
    birth = Landmark(year=1823, qualifier="about", place=brest)
    request = build_request(_facts(landmarks=[death, birth], occupation="marin"), "fr")

    assert request.lines == [
        "vers 1823 – Plouguerneau (Finistère, France)",
        "1891 – Brest (Finistère, France)",
    ]
    text = "\n".join(request.lines)
    for forbidden in ("Jean", "Gall", "I0042", "H123", "naiss", "décès", "29880", "marin"):
        assert forbidden not in text
    assert request.occupation is None
    assert (request.year_min, request.year_max) == (1823, 1891)


def test_occupation_is_sent_only_when_enabled(brest):
    facts = _facts(landmarks=[Landmark(year=1850, place=brest)], occupation="cultivateur")
    assert build_request(facts, "fr", include_occupation=True).occupation == "cultivateur"
    assert build_request(facts, "fr", include_occupation=False).occupation is None


def test_department_is_completed_from_the_code(brest):
    request = build_request(_facts(landmarks=[Landmark(year=1850, place=brest)]))
    assert request.places[0].department == "Finistère"
    assert request.places[0].code == "29880"


def test_duplicates_and_unknown_values():
    place = PlaceInfo(name="Brest", country="France")
    facts = _facts(landmarks=[Landmark(year=1900, place=place), Landmark(year=1900, place=place),
                              Landmark(place=None, year=None), Landmark(place=place)])
    request = build_request(facts, "en")
    assert request.lines == ["1900 – Brest (France)", "unknown date – Brest (France)"]
    assert len(request.places) == 1


def test_year_formats():
    assert format_year(Landmark(year=1820, year_end=1825, qualifier="between")) == \
        "entre 1820 et 1825"
    assert format_year(Landmark(year=1820, qualifier="before"), "en") == "before 1820"
    assert format_year(Landmark(year=None)) == "date inconnue"


def test_period_bounds():
    assert period_bounds(ContextRequest(lines=[], year_min=1800, year_max=1870)) == (1795, 1870)
    assert period_bounds(ContextRequest(lines=[], year_min=1800, year_max=1800)) == (1770, 1830)
    assert period_bounds(ContextRequest(lines=[])) == (None, None)
