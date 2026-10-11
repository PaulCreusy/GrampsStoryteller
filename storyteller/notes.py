"""
Write the generated context in a Gramps note attached to the person.

The note carries the "Storyteller" tag; when the person already has such a
note, its text is replaced.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import Optional, Tuple

# Dependencies #
from gramps.gen.db import DbTxn
from gramps.gen.lib import Note, NoteType, StyledText, StyledTextTag, StyledTextTagType, Tag

# Local imports #
from .i18n import _
from .note_content import NoteContent

#############
# Constants #
#############

TAG_NAME = "Storyteller"

_TAG_KINDS = {
    "bold": StyledTextTagType.BOLD,
    "italic": StyledTextTagType.ITALIC,
    "link": StyledTextTagType.LINK,
}

#############
# Functions #
#############


def note_type() -> NoteType:
    """
    Return the custom note type of the generated notes.

    Returns
    -------
    NoteType
        Custom "Life story" type.
    """
    the_type = NoteType()
    the_type.set((NoteType.CUSTOM, _("Life story")))
    return the_type


def styled_text(content: NoteContent) -> StyledText:
    """
    Convert a NoteContent into a Gramps StyledText.

    Parameters
    ----------
    content : NoteContent
        Text and formatting.

    Returns
    -------
    StyledText
        Styled text.
    """
    tags = []
    for kind, value, start, end in content.tags:
        tag_type = StyledTextTagType(_TAG_KINDS[kind])
        tags.append(StyledTextTag(tag_type, value if kind == "link" else None, [(start, end)]))
    return StyledText(content.text, tags)


def _get_or_create_tag(db, trans) -> str:
    """
    Return the handle of the Storyteller tag, creating it if needed.

    Parameters
    ----------
    db : DbWriteBase
        Database.
    trans : DbTxn
        Transaction.

    Returns
    -------
    str
        Tag handle.
    """
    tag = db.get_tag_from_name(TAG_NAME)
    if tag is None:
        tag = Tag()
        tag.set_name(TAG_NAME)
        tag.set_color("#7a5c3e")
        db.add_tag(tag, trans)
    return tag.get_handle()


def find_story_note(db, person, tag_handle: Optional[str]):
    """
    Return the existing Storyteller note of a person.

    Parameters
    ----------
    db : DbReadBase
        Database.
    person : Person
        Person.
    tag_handle : str or None
        Handle of the Storyteller tag.

    Returns
    -------
    Note or None
        Note, None when the person has none.
    """
    if not tag_handle:
        return None
    for note_handle in person.get_note_list():
        note = db.get_note_from_handle(note_handle)
        if note is not None and tag_handle in note.get_tag_list():
            return note
    return None


def write_story_note(db, person_handle: str, content: NoteContent,
                     person_name: str = "") -> Tuple[str, bool]:
    """
    Create or replace the Storyteller note of a person, in one undoable
    transaction.

    Parameters
    ----------
    db : DbWriteBase
        Database.
    person_handle : str
        Person handle.
    content : NoteContent
        Note text and formatting.
    person_name : str
        Name used in the undo history label.

    Returns
    -------
    tuple of (str, bool)
        Note handle and whether an existing note was replaced.
    """
    label = _("Storyteller: context of %s") % (person_name or person_handle)
    with DbTxn(label, db) as trans:
        person = db.get_person_from_handle(person_handle)
        tag_handle = _get_or_create_tag(db, trans)
        note = find_story_note(db, person, tag_handle)
        replaced = note is not None
        if note is None:
            note = Note()
            note.set_type(note_type())
            note.add_tag(tag_handle)
            note.set_styledtext(styled_text(content))
            db.add_note(note, trans)
            person.add_note(note.get_handle())
            db.commit_person(person, trans)
        else:
            note.set_styledtext(styled_text(content))
            db.commit_note(note, trans)
    return note.get_handle(), replaced
