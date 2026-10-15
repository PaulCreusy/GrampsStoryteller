"""
Tests of the article section parsing and period filtering.
"""

###########
# Imports #
###########

# Local imports #
from storyteller.sources.sections import (
    era_spans, history_sections, lead_text, parse_sections, select_paragraphs, time_spans)

#############
# Constants #
#############

ARTICLE = """Plouguerneau est une commune du Finistère.
Elle est située sur la côte des Abers.

== Géographie ==
Le territoire est littoral.

== Histoire ==
=== Moyen Âge ===
La paroisse est mentionnée en 1330.
Un prieuré y est fondé.
=== Époque moderne ===
Au XVIIe siècle, le goémon est récolté.
=== Révolution française ===
La commune est créée en 1790.
Les prêtres réfractaires s'y cachent.
=== XIXe siècle ===
En 1832, le choléra frappe la commune.
Les habitants vivent de la pêche et de l'agriculture.
En 1960, le port est modernisé.
=== Époque récente ===
En 1880, une école est ouverte.
Un phare est construit.
La commune fusionne ensuite avec sa voisine.

== Personnalités liées à la commune ==
Personne célèbre née en 1850.
"""

#############
# Functions #
#############


def test_parse_and_history_sections():
    sections = parse_sections(ARTICLE)
    assert sections[0].title == ""
    assert sections[0].paragraphs[0].startswith("Plouguerneau")
    titles = [s.title for s in history_sections(sections)]
    assert titles == ["Histoire", "Moyen Âge", "Époque moderne", "Révolution française",
                      "XIXe siècle", "Époque récente"]


def test_time_spans():
    assert (1832, 1832) in time_spans("En 1832, le choléra")
    assert (1601, 1700) in time_spans("Au XVIIe siècle")
    assert (1801, 1900) in time_spans("in the 19th century")
    assert (1830, 1839) in time_spans("during the 1830s")
    assert time_spans("12 000 habitants") == []


def test_era_spans():
    assert era_spans("Moyen Âge") == [(476, 1492)]
    assert era_spans("Seconde Guerre mondiale") == [(1939, 1945)]
    assert era_spans("Géographie") == []


def test_select_paragraphs_keeps_the_period():
    sections = history_sections(parse_sections(ARTICLE))
    text = select_paragraphs(sections, 1813, 1891, 5000)
    assert "1832" in text
    assert "pêche" in text  # undated paragraph following a kept one
    assert "— XIXe siècle —" in text
    assert "1330" not in text
    assert "1960" not in text
    assert "goémon" not in text


def test_select_paragraphs_uses_era_headings():
    sections = history_sections(parse_sections(ARTICLE))
    text = select_paragraphs(sections, 1789, 1795, 5000)
    assert "réfractaires" in text
    assert "1790" in text


def test_budget_is_respected():
    sections = history_sections(parse_sections(ARTICLE))
    text = select_paragraphs(sections, None, None, 60)
    assert len(text) <= 60
    assert lead_text(parse_sections(ARTICLE), 1000).startswith("Plouguerneau")


def test_undated_paragraphs_do_not_chain():
    sections = history_sections(parse_sections(ARTICLE))
    text = select_paragraphs(sections, 1813, 1891, 5000)
    assert "école" in text
    assert "phare" in text
    assert "fusionne" not in text


def test_broad_headings_do_not_keep_undated_paragraphs():
    article = (
        "Intro.\n== Histoire ==\n"
        "=== XIXe siècle ===\nLes forges emploient de nombreux ouvriers.\n"
        "=== XXe siècle et XXIe siècle ===\nCette fusion sera mal perçue par la population.\n"
        "En 1920, la région se reconstruit.\n"
    )
    text = select_paragraphs(history_sections(parse_sections(article)), 1868, 1924, 5000)
    assert "forges" in text
    assert "fusion" not in text
    assert "1920" in text


def test_bibliographic_references_are_dropped():
    article = ("Intro.\n== Histoire ==\n=== Contemporaine ===\n"
               "Vincent Petit, Catholiques et Comtois. Liturgie diocésaine et identité régionale "
               "au XIXe siècle, Cerf, 2011\n"
               "Au XIXe siècle, la région compte de nombreuses filatures et la population "
               "ouvrière augmente fortement dans les vallées, notamment autour des forges, "
               "selon les recensements publiés en 1891.\n")
    text = select_paragraphs(history_sections(parse_sections(article)), 1878, 1924, 5000)
    assert "Cerf" not in text
    assert "filatures" in text


def test_long_periods_are_covered_to_their_end():
    filler = " Les notables de la ville débattent longuement de projets d'embellissement."
    article = "Intro.\n== Histoire ==\n" + "\n".join(
        "En %d, le conseil municipal se réunit.%s" % (year, filler * 3)
        for year in range(1885, 1910)) + (
        "\nEn 1914, le département est occupé par l'armée allemande et les habitants subissent "
        "les réquisitions.\nEn 1925, les houillères emploient des milliers de mineurs.\n"
        "En 1941, la grève des mineurs prive l'occupant de charbon.")
    sections = history_sections(parse_sections(article))
    text = select_paragraphs(sections, 1879, 1960, 2500)
    assert len(text) <= 2500
    assert "occupé par l'armée allemande" in text
    assert "houillères" in text
    assert "grève des mineurs" in text
    assert "En 1885" in text  # the beginning of the period is still covered
    # Article order is kept
    assert text.index("En 1885") < text.index("1914") < text.index("1941")
