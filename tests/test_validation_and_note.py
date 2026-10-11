"""
Tests of the local checks and of the note content.
"""

###########
# Imports #
###########

# Python imports #
import datetime

# Local imports #
from storyteller.models import SourceExtract
from storyteller.note_content import build_note_content
from storyteller.validation import (
    check_locally, cited_numbers, parse_llm_validation, split_general_knowledge)

#############
# Functions #
#############


def test_cited_numbers():
    assert cited_numbers("a [1], b [2][3], c [4-6], d [7, 9]") == {1, 2, 3, 4, 5, 6, 7, 9}


def test_split_general_knowledge():
    segments, balanced = split_general_knowledge("A [1]. <cg>B.</cg> C [2].")
    assert segments == [("A [1]. ", False), ("B.", True), (" C [2].", False)]
    assert balanced
    assert not split_general_knowledge("A <cg>B")[1]


def test_local_checks_find_problems():
    text = "En 1832, le choléra [1]. En 1847, une famine [5]. <cg>En 1848, une révolution.</cg>"
    report = check_locally(text, 2, "En 1832, le choléra frappe.", {1823}, rich=True,
                           truncated=True, lang="fr")
    joined = "\n".join(report.warnings)
    assert report.ok
    assert "[5]" in joined
    assert "1847" in joined
    assert "1848" not in joined  # general knowledge years are not checked
    assert "jugées riches" in joined
    assert "tronqué" in joined


def test_empty_text_is_an_error():
    report = check_locally("  ", 1, "", set(), False, False, "en")
    assert not report.ok


def test_parse_llm_validation():
    assert parse_llm_validation('```json\n{"ok": true, "problemes": []}\n```') == (True, [])
    assert parse_llm_validation('{"ok": false, "problemes": ["x"]}') == (False, ["x"])
    assert parse_llm_validation("pas de JSON") is None


def test_note_content():
    sources = [
        SourceExtract("Plouguerneau", "commune", "https://fr.wikipedia.org/w/index.php?oldid=1",
                      "...", 1),
        SourceExtract("Bretagne", "province", "https://fr.wikipedia.org/w/index.php?oldid=2",
                      "...", 2),
    ]
    content = build_note_content(
        "Le port est actif [1]. <cg>La France est un royaume.</cg> Voir https://x.y",
        sources, "OpenAI", "gpt-5-mini", "fr", datetime.date(2026, 10, 11))

    assert "https://x.y" not in content.text
    assert "La France est un royaume. [connaissance générale, non sourcée]" in content.text
    assert "[1] Plouguerneau (commune) — https://fr.wikipedia.org" in content.text
    assert "Bretagne (province historique)" not in content.text  # not cited
    assert content.text.endswith("Généré le 11/10/2026 — OpenAI · gpt-5-mini")
    kinds = {kind for kind, _value, _start, _end in content.tags}
    assert kinds == {"italic", "bold", "link"}
    for kind, value, start, end in content.tags:
        if kind == "link":
            assert content.text[start:end] == value
        if kind == "bold":
            assert content.text[start:end] == "Sources"
