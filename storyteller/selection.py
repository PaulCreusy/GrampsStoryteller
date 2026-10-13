"""
Selection of the persons to process in a Gramps database: active person,
selection of the People view, ancestors, descendants, custom filter or
whole tree.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import Iterable, List, Optional

# Local imports #
from .notes import TAG_NAME, find_story_note

#############
# Constants #
#############

SCOPE_ACTIVE = "active"
SCOPE_SELECTED = "selected"
SCOPE_ANCESTORS = "ancestors"
SCOPE_DESCENDANTS = "descendants"
SCOPE_FILTER = "filter"
SCOPE_ALL = "all"

SCOPES = (SCOPE_ACTIVE, SCOPE_SELECTED, SCOPE_ANCESTORS, SCOPE_DESCENDANTS, SCOPE_FILTER,
          SCOPE_ALL)
GENERATION_SCOPES = (SCOPE_ANCESTORS, SCOPE_DESCENDANTS)

#############
# Functions #
#############


def _unique(handles: Iterable[str]) -> List[str]:
    """
    Remove the duplicates and empty handles, keeping the order.

    Parameters
    ----------
    handles : iterable of str
        Handles.

    Returns
    -------
    list of str
        Unique handles.
    """
    seen = set()
    result = []
    for handle in handles:
        if handle and handle not in seen:
            seen.add(handle)
            result.append(handle)
    return result


def ancestors(db, handle: str, generations: int) -> List[str]:
    """
    Return a person and their ancestors, generation by generation.

    Parameters
    ----------
    db : DbReadBase
        Database.
    handle : str
        Handle of the starting person (generation 1).
    generations : int
        Number of generations, the starting person included.

    Returns
    -------
    list of str
        Person handles.
    """
    result = [handle]
    seen = {handle}
    current = [handle]
    for _generation in range(1, generations):
        parents = []
        for person_handle in current:
            person = db.get_person_from_handle(person_handle)
            if person is None:
                continue
            for family_handle in person.get_parent_family_handle_list():
                family = db.get_family_from_handle(family_handle)
                if family is None:
                    continue
                for parent in (family.get_father_handle(), family.get_mother_handle()):
                    if parent and parent not in seen:
                        seen.add(parent)
                        parents.append(parent)
        if not parents:
            break
        result.extend(parents)
        current = parents
    return result


def descendants(db, handle: str, generations: int) -> List[str]:
    """
    Return a person and their descendants, generation by generation.

    Parameters
    ----------
    db : DbReadBase
        Database.
    handle : str
        Handle of the starting person (generation 1).
    generations : int
        Number of generations, the starting person included.

    Returns
    -------
    list of str
        Person handles.
    """
    result = [handle]
    seen = {handle}
    current = [handle]
    for _generation in range(1, generations):
        children = []
        for person_handle in current:
            person = db.get_person_from_handle(person_handle)
            if person is None:
                continue
            for family_handle in person.get_family_handle_list():
                family = db.get_family_from_handle(family_handle)
                if family is None:
                    continue
                for child_ref in family.get_child_ref_list():
                    if child_ref.ref not in seen:
                        seen.add(child_ref.ref)
                        children.append(child_ref.ref)
        if not children:
            break
        result.extend(children)
        current = children
    return result


def all_people(db) -> List[str]:
    """
    Return every person of the tree, sorted by name.

    Parameters
    ----------
    db : DbReadBase
        Database.

    Returns
    -------
    list of str
        Person handles.
    """
    return list(db.get_person_handles(sort_handles=True))


def custom_filters() -> list:
    """
    Return the custom person filters defined by the user.

    Returns
    -------
    list of GenericFilter
        Filters.
    """
    from gramps.gen import filters
    if filters.CustomFilters is None:
        filters.reload_custom_filters()
    return list(filters.CustomFilters.get_filters("Person"))


def filtered_people(db, person_filter, user=None) -> List[str]:
    """
    Return the persons matching a filter, sorted by name.

    Parameters
    ----------
    db : DbReadBase
        Database.
    person_filter : GenericFilter
        Gramps person filter.
    user : User, optional
        Gramps user (progress of the filter).

    Returns
    -------
    list of str
        Person handles.
    """
    return person_filter.apply(db, all_people(db), user=user)


def selected_people(uistate) -> List[str]:
    """
    Return the persons selected in the People view.

    Parameters
    ----------
    uistate : DisplayState
        Gramps interface state.

    Returns
    -------
    list of str
        Person handles, empty when the active view does not show persons.
    """
    page = getattr(getattr(uistate, "viewmanager", None), "active_page", None)
    if page is None:
        return []
    try:
        if page.navigation_type() != "Person":
            return []
        return _unique(page.selected_handles())
    except (AttributeError, TypeError):
        return []


def people_for_scope(db, uistate, scope: str, generations: int = 4,
                     person_filter=None, user=None) -> List[str]:
    """
    Return the persons of a selection scope.

    Parameters
    ----------
    db : DbReadBase
        Database.
    uistate : DisplayState
        Gramps interface state.
    scope : str
        One of SCOPES.
    generations : int
        Generations for the ancestors and descendants scopes.
    person_filter : GenericFilter, optional
        Filter of the custom filter scope.
    user : User, optional
        Gramps user.

    Returns
    -------
    list of str
        Person handles, in a stable order.
    """
    active: Optional[str] = uistate.get_active("Person") if uistate is not None else None
    if scope == SCOPE_ACTIVE:
        return [active] if active else []
    if scope == SCOPE_SELECTED:
        return selected_people(uistate)
    if scope == SCOPE_ANCESTORS:
        return ancestors(db, active, generations) if active else []
    if scope == SCOPE_DESCENDANTS:
        return descendants(db, active, generations) if active else []
    if scope == SCOPE_FILTER:
        return filtered_people(db, person_filter, user) if person_filter is not None else []
    if scope == SCOPE_ALL:
        return all_people(db)
    raise ValueError(scope)


def persons_with_story_note(db, handles: Iterable[str]) -> set:
    """
    Return the persons that already have a Storyteller note.

    Parameters
    ----------
    db : DbReadBase
        Database.
    handles : iterable of str
        Person handles.

    Returns
    -------
    set of str
        Handles of the persons with a note.
    """
    tag = db.get_tag_from_name(TAG_NAME)
    if tag is None:
        return set()
    tag_handle = tag.get_handle()
    result = set()
    for handle in handles:
        person = db.get_person_from_handle(handle)
        if person is not None and find_story_note(db, person, tag_handle) is not None:
            result.add(handle)
    return result
