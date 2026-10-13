"""
Integration tests against a real Gramps database (the example tree shipped
with the Gramps sources). Skipped when Gramps is not importable.

Set GRAMPS_EXAMPLE to the path of example.gramps if it is not found
automatically.
"""

###########
# Imports #
###########

# Python imports #
import contextlib
import io
import os

# Dependencies #
import pytest

pytest.importorskip("gramps.gen.lib")

import gramps  # noqa: E402

from gramps.cli.user import User  # noqa: E402
from gramps.gen.db.utils import import_as_dict  # noqa: E402
from gramps.gen.lib import (  # noqa: E402
    ChildRef, Date, Event, EventRef, EventType, Family, Person, Place, PlaceName, PlaceRef,
    PlaceType)
from gramps.gen.db import DbTxn  # noqa: E402

# Local imports #
from storyteller.anonymizer import build_request  # noqa: E402
from storyteller.extraction import (  # noqa: E402
    SKIP_LIVING, SKIP_NO_LANDMARK, SKIP_PRIVATE, extract_person)
from storyteller.note_content import build_note_content  # noqa: E402
from storyteller.notes import TAG_NAME, write_story_note  # noqa: E402
from storyteller.selection import (  # noqa: E402
    SCOPE_ACTIVE, SCOPE_ALL, SCOPE_ANCESTORS, SCOPE_DESCENDANTS, ancestors, descendants,
    people_for_scope, persons_with_story_note)

#############
# Functions #
#############


def _example_path():
    candidates = [os.environ.get("GRAMPS_EXAMPLE", "")]
    root = os.path.dirname(os.path.dirname(os.path.abspath(gramps.__file__)))
    candidates.append(os.path.join(root, "example", "gramps", "example.gramps"))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


@pytest.fixture(scope="module")
def example_db():
    path = _example_path()
    if path is None:
        pytest.skip("example.gramps not found (set GRAMPS_EXAMPLE)")
    with contextlib.redirect_stdout(io.StringIO()):
        return import_as_dict(path, User())


@pytest.fixture
def empty_db():
    from gramps.gen.db.utils import make_database
    db = make_database("sqlite")
    db.load(":memory:")
    return db


def _add_place(db, trans, name, place_type, parent=None, code=""):
    place = Place()
    place.set_name(PlaceName(value=name))
    place.set_type(place_type)
    place.set_code(code)
    if parent is not None:
        ref = PlaceRef()
        ref.ref = parent.get_handle()
        place.add_placeref(ref)
    db.add_place(place, trans)
    return place


def _add_event(db, trans, person, event_type, year, place, private=False):
    event = Event()
    event.set_type(event_type)
    date = Date()
    date.set_yr_mon_day(year, 1, 1)
    event.set_date_object(date)
    event.set_place_handle(place.get_handle() if place else None)
    event.set_privacy(private)
    db.add_event(event, trans)
    ref = EventRef()
    ref.ref = event.get_handle()
    person.add_event_ref(ref)
    if event_type == EventType.BIRTH:
        person.set_birth_ref(ref)
    if event_type == EventType.DEATH:
        person.set_death_ref(ref)


def test_example_tree_excludes_living_people(example_db):
    reasons = {}
    for person in example_db.iter_people():
        facts = extract_person(example_db, person, "x")
        reasons[facts.skip_reason] = reasons.get(facts.skip_reason, 0) + 1
        if facts.skip_reason is None:
            request = build_request(facts, "fr")
            text = "\n".join(request.lines)
            assert person.get_gramps_id() not in text
            for label in ("naissance", "baptême", "décès", "inhumation"):
                assert label not in text
    assert reasons.get(None, 0) > 100
    assert reasons.get(SKIP_LIVING, 0) > 100


def test_hierarchy_code_fallbacks_and_privacy(empty_db):
    db = empty_db
    with DbTxn("setup", db) as trans:
        france = _add_place(db, trans, "France", PlaceType.COUNTRY)
        finistere = _add_place(db, trans, "Finistère", PlaceType.DEPARTMENT, france)
        commune = _add_place(db, trans, "Plouguerneau", PlaceType.CITY, finistere, "29880")
        church = _add_place(db, trans, "Église Saint-Pierre", PlaceType.BUILDING, commune)
        brest = _add_place(db, trans, "Brest", PlaceType.CITY, finistere)

        person = Person()
        _add_event(db, trans, person, EventType.BAPTISM, 1823, church)
        _add_event(db, trans, person, EventType.BURIAL, 1891, brest)
        _add_event(db, trans, person, EventType.DEATH, 1890, brest, private=True)
        db.add_person(person, trans)

        private = Person()
        private.set_privacy(True)
        db.add_person(private, trans)
        nothing = Person()
        _add_event(db, trans, nothing, EventType.BIRTH, 1700, None)
        db.add_person(nothing, trans)

    facts = extract_person(db, db.get_person_from_handle(person.get_handle()), "x", True)
    assert facts.skip_reason is None
    baptism, burial = facts.landmarks
    assert (baptism.year, baptism.place.name, baptism.place.code) == (1823, "Plouguerneau", "29880")
    assert baptism.place.department == "Finistère"
    assert baptism.place.country == "France"
    assert burial.year == 1891  # the private death event is ignored

    assert extract_person(db, db.get_person_from_handle(private.get_handle())).skip_reason == \
        SKIP_PRIVATE
    other = extract_person(db, db.get_person_from_handle(nothing.get_handle()))
    assert other.skip_reason in (None, SKIP_NO_LANDMARK)


def test_note_is_created_then_replaced(empty_db):
    db = empty_db
    with DbTxn("setup", db) as trans:
        person = Person()
        db.add_person(person, trans)
    first = build_note_content("Premier [1].", [], "OpenAI", "m", "fr")
    second = build_note_content("Second <cg>général</cg>.", [], "OpenAI", "m", "fr")

    handle, replaced = write_story_note(db, person.get_handle(), first, "x")
    assert not replaced
    handle2, replaced = write_story_note(db, person.get_handle(), second, "x")
    assert replaced and handle2 == handle

    person = db.get_person_from_handle(person.get_handle())
    assert person.get_note_list() == [handle]
    note = db.get_note_from_handle(handle)
    assert note.get().startswith("Second général.")
    assert "En italique : connaissances générales" in note.get()
    assert db.get_tag_from_name(TAG_NAME).get_handle() in note.get_tag_list()
    assert note.get_type().xml_str() == "Life story"
    tags = note.get_styledtext().get_tags()
    assert any(int(tag.name) == tag.name.ITALIC for tag in tags)


class _UiState:
    def __init__(self, active):
        self.active = active

    def get_active(self, navigation_type):
        return self.active


def _family(db, trans, father, mother, children):
    family = Family()
    family.set_father_handle(father.get_handle())
    family.set_mother_handle(mother.get_handle())
    for child in children:
        ref = ChildRef()
        ref.ref = child.get_handle()
        family.add_child_ref(ref)
    db.add_family(family, trans)
    for parent in (father, mother):
        parent.add_family_handle(family.get_handle())
        db.commit_person(parent, trans)
    for child in children:
        child.add_parent_family_handle(family.get_handle())
        db.commit_person(child, trans)


def test_selection_scopes(empty_db):
    db = empty_db
    with DbTxn("setup", db) as trans:
        people = {}
        for key in ("gf", "gm", "f", "m", "me", "mate", "child", "mate2", "grandchild"):
            person = Person()
            db.add_person(person, trans)
            people[key] = person
        _family(db, trans, people["gf"], people["gm"], [people["f"]])
        _family(db, trans, people["f"], people["m"], [people["me"]])
        _family(db, trans, people["me"], people["mate"], [people["child"]])
        _family(db, trans, people["child"], people["mate2"], [people["grandchild"]])
        handle = {key: person.get_handle() for key, person in people.items()}

    by_handle = {v: k for k, v in handle.items()}
    names = lambda handles: [by_handle.get(h, "?") for h in handles]  # noqa: E731
    assert names(ancestors(db, handle["me"], 1)) == ["me"]
    assert names(ancestors(db, handle["me"], 2)) == ["me", "f", "m"]
    assert names(ancestors(db, handle["me"], 10)) == ["me", "f", "m", "gf", "gm"]
    assert names(descendants(db, handle["gf"], 3)) == ["gf", "f", "me"]
    assert names(descendants(db, handle["me"], 10)) == ["me", "child", "grandchild"]

    ui = _UiState(handle["me"])
    assert people_for_scope(db, ui, SCOPE_ACTIVE) == [handle["me"]]
    assert names(people_for_scope(db, ui, SCOPE_ANCESTORS, 2)) == ["me", "f", "m"]
    assert names(people_for_scope(db, ui, SCOPE_DESCENDANTS, 2)) == ["me", "child"]
    assert len(people_for_scope(db, ui, SCOPE_ALL)) == 9
    assert people_for_scope(db, _UiState(None), SCOPE_ANCESTORS) == []

    assert persons_with_story_note(db, handle.values()) == set()
    content = build_note_content("Texte.", [], "OpenAI", "m", "fr")
    write_story_note(db, handle["f"], content, "f")
    assert persons_with_story_note(db, handle.values()) == {handle["f"]}
