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
    assert "1 passage(s) de connaissances générales" in joined
    assert "tronqué" in joined


def test_empty_text_is_an_error():
    report = check_locally("  ", 1, "", set(), False, False, "en")
    assert not report.ok


def test_parse_llm_validation():
    assert parse_llm_validation('```json\n{"ok": true, "problemes": []}\n```') == (True, [])
    assert parse_llm_validation('{"ok": false, "problemes": ["x"]}') == (False, ["x"])
    assert parse_llm_validation("pas de JSON") is None


def test_parse_llm_validation_tolerates_small_model_answers():
    # Reasoning containing braces before the answer
    raw = ('<think>Le format attendu est {"ok": ..., "problemes": [...]}. '
           'Je vérifie {chaque phrase}.</think>\n'
           '{"ok": false, "problemes": ["« Lille » : absent des extraits"]}')
    assert parse_llm_validation(raw) == (False, ["« Lille » : absent des extraits"])
    # Template that adds the opening tag itself
    assert parse_llm_validation('Je réfléchis {x}.</think>{"ok": true, "problemes": []}') == \
        (True, [])
    # Unescaped double quotes inside the problems
    raw = '{"ok": false, "problemes": ["\"la région\" n\'est pas la commune", ' \
          '""machines à vapeur" : non sourcé"]}'
    assert parse_llm_validation(raw) == \
        (False, ['"la région" n\'est pas la commune', '"machines à vapeur" : non sourcé'])
    raw = '{"ok": false, "problemes": [\n  "Le texte dit "instable" à tort",\n  "Digression"\n]}'
    assert parse_llm_validation(raw) == (False, ['Le texte dit "instable" à tort', "Digression"])
    # Trailing comma, Python booleans, text around, accented key
    assert parse_llm_validation('Voici : {"ok": True, "problèmes": [],} Fin.') == (True, [])
    assert parse_llm_validation('{"ok": false, "problemes": ["a", "b",]}') == \
        (False, ["a", "b"])
    # Reasoning cut by the token limit: no answer
    assert parse_llm_validation("<think>Je commence à vérifier {") is None


def test_unreadable_answer_is_quoted_in_the_warning():
    from storyteller.validation import answer_excerpt
    assert answer_excerpt("a\n  b " + "x" * 300, 10) == "a b xxxxxx…"


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
    assert "La France est un royaume." in content.text
    assert "connaissance générale" not in content.text.split("\n")[0]
    assert "En italique : connaissances générales" in content.text
    italic = [content.text[s:e] for kind, _v, s, e in content.tags if kind == "italic"]
    assert "La France est un royaume." in italic
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


def test_no_legend_without_general_knowledge():
    content = build_note_content("Texte sourcé.", [], "OpenAI", "m", "fr")
    assert "En italique" not in content.text


def test_general_knowledge_is_not_blamed_for_missing_from_the_excerpts():
    from storyteller.validation import drop_unfounded_cg_problems
    text = ("Le Nord devient la première usine de France. <cg>La pratique religieuse demeure "
            "un élément important de la vie sociale.</cg> <cg>Le pain est à base de seigle "
            "depuis 1950.</cg>")
    problems = [
        "La pratique religieuse demeure un élément important de la vie sociale : affirmation "
        "non contenue dans les extraits",
        "« Le pain est à base de seigle depuis 1950 » : anachronique",
        "« la première usine de France » : absent des extraits",
    ]
    assert drop_unfounded_cg_problems(text, problems) == problems[1:]


def test_stray_quotes_do_not_hide_general_knowledge():
    from storyteller.validation import drop_unfounded_cg_problems, tidy_problem
    text = ("Le Nord est industriel. <cg>Cette région est historiquement caractérisée par une "
            "forte activité industrielle et agricole.</cg>")
    problem = ('Cette région est historiquement caractérisée par une forte activité '
               'industrielle et agricole." : passage hors balises non contenu dans les extraits')
    assert drop_unfounded_cg_problems(text, [problem]) == []
    assert tidy_problem('Le département est marqué" : non sourcé') == \
        "« Le département est marqué » : non sourcé"
    assert tidy_problem("« Lille » : absent") == "« Lille » : absent"
    assert tidy_problem("Digression", "en") == "Digression"


def test_cut_tags_are_repaired():
    from storyteller.validation import repair_cg_tags, tags_balanced
    assert repair_cg_tags("<cg>Texte.</cg") == "<cg>Texte.</cg>"
    assert repair_cg_tags("<cg Texte.</cg>") == "<cg>Texte.</cg>"
    assert not tags_balanced("Texte.</cg>")
    assert tags_balanced("<cg>A</cg> B")
