"""
Tests of the sourced context collection and its fallbacks.
"""

###########
# Imports #
###########

# Local imports #
from conftest import FakeWikipedia
from storyteller.anonymizer import build_request
from storyteller.models import Landmark, PersonFacts, PlaceInfo
from storyteller.sources.hierarchy import SourceCollector

#############
# Constants #
#############

LONG_HISTORY = "\n".join("En %d, un événement local important a lieu dans le département et "
                         "transforme durablement la vie des habitants du territoire." % year
                         for year in range(1800, 1890, 3))

PAGES = {
    "Finistère": "Le Finistère est un département français.\n== Histoire ==\n" + LONG_HISTORY,
    "Bretagne (province)": "La Bretagne est une province du royaume de France.\n"
                           "== Histoire ==\nEn 1789, la province est supprimée.\n"
                           "En 1840, la Bretagne commence à s'industrialiser.",
    "Monarchie de Juillet": "La monarchie de Juillet est le régime de la France de 1830 à 1848.",
    "Restauration (histoire de France)": "La Restauration est le régime de 1814 à 1830.",
    "Deuxième République (France)": "La Deuxième République dure de 1848 à 1852.",
    "Second Empire": "Le Second Empire dure de 1852 à 1870.",
}

#############
# Functions #
#############


def _request(place, years=(1823, 1860)):
    facts = PersonFacts(handle="h", gramps_id="I1", name="x",
                        landmarks=[Landmark(year=y, place=place) for y in years])
    return build_request(facts, "fr")


def test_small_commune_falls_back_to_department_and_province():
    wiki = FakeWikipedia(PAGES)
    place = PlaceInfo(name="Kerlouan-Minuscule", country="France", code="29890")
    result = SourceCollector(wiki).collect(_request(place))

    levels = [(e.level, e.title) for e in result.extracts]
    assert ("department", "Finistère") in levels
    assert ("province", "Bretagne (province)") in levels
    assert ("period", "Monarchie de Juillet") in levels
    assert [e.number for e in result.extracts] == list(range(1, len(result.extracts) + 1))
    assert any("Kerlouan-Minuscule" in w for w in result.warnings)
    # The department history alone is rich enough
    assert result.rich
    # The code is never used as a search term
    assert not any("29890" in r for r in wiki.requested)


def test_commune_with_department_suffix_and_disambiguation():
    pages = dict(PAGES)
    pages["Saint-Pierre"] = "Saint-Pierre peut désigner plusieurs communes."
    pages["Saint-Pierre (Finistère)"] = (
        "Saint-Pierre est une commune du Finistère.\n== Histoire ==\nEn 1850, rien.")
    wiki = FakeWikipedia(pages, disambiguation={"Saint-Pierre"})
    place = PlaceInfo(name="Saint-Pierre", country="France", code="29000")
    result = SourceCollector(wiki).collect(_request(place))
    assert result.extracts[0].title == "Saint-Pierre (Finistère)"
    assert result.extracts[0].level == "commune"


def test_search_results_must_match_the_place_name():
    pages = dict(PAGES)
    pages["Plouguerneau"] = "Plouguerneau est une commune du Finistère.\n== Histoire ==\n" \
                            "En 1830, le port est actif."
    wiki = FakeWikipedia(pages, search_results={
        "Plouguernau Finistère": ["Plouguernau (homonymie)", "Plouguerneau"]})
    place = PlaceInfo(name="Plouguernau", country="France", code="29880")
    # The misspelled name only matches the search results starting with it
    result = SourceCollector(wiki).collect(_request(place))
    assert "Plouguerneau" not in [e.title for e in result.extracts]


def test_code_conflict_prefers_the_hierarchy():
    wiki = FakeWikipedia(PAGES)
    place = PlaceInfo(name="X", department="Finistère", country="France", code="56000")
    result = SourceCollector(wiki).collect(_request(place))
    assert any("56000" in w for w in result.warnings)
    assert any(e.title == "Finistère" for e in result.extracts)


def test_foreign_place_uses_hierarchy_and_country_history():
    pages = {
        "Liège": "Liège est une ville de Belgique.\n== Histoire ==\nEn 1830, la ville se révolte.",
        "Histoire de la Belgique": "Intro.\n== XIXe siècle ==\nEn 1830, indépendance.\n"
                                   "== Moyen Âge ==\nEn 1200, un comté.",
    }
    wiki = FakeWikipedia(pages)
    place = PlaceInfo(name="Liège", country="Belgique")
    result = SourceCollector(wiki).collect(_request(place, years=(1825, 1880)))
    titles = {e.title: e for e in result.extracts}
    assert "Liège" in titles
    assert "1830" in titles["Histoire de la Belgique"].text
    assert "1200" not in titles["Histoire de la Belgique"].text
    assert not any(e.level == "period" for e in result.extracts)


def test_poor_sources_are_not_rich():
    wiki = FakeWikipedia({"Bretagne (province)": PAGES["Bretagne (province)"]})
    place = PlaceInfo(name="Inconnu", country="France", code="29000")
    result = SourceCollector(wiki).collect(_request(place))
    assert not result.rich


def test_thin_department_history_uses_the_history_article():
    pages = {
        "Conflandey (Haute-Saône)": "Conflandey est une commune de la Haute-Saône.\n"
                                    "== Histoire ==\nEn 1897, la papeterie devient une tréfilerie.",
        "Haute-Saône": "La Haute-Saône est un département français.\n== Histoire ==\n"
                       "Le département est créé en 1790.",
        "Histoire de la Haute-Saône": "Intro.\n== Époque contemporaine ==\n"
                                      "En 1880, de nombreux Alsaciens s'installent dans le "
                                      "département.\n== Moyen Âge ==\nEn 1200, des abbayes.",
        "Franche-Comté (province)": "La Franche-Comté est une province de France.\n"
                                    "== Histoire ==\n=== XXe siècle et XXIe siècle ===\n"
                                    "Cette fusion sera mal perçue par la population.",
    }
    wiki = FakeWikipedia(pages)
    place = PlaceInfo(name="Conflandey", country="France", code="70170")
    result = SourceCollector(wiki).collect(_request(place, years=(1878, 1924)))
    by_title = {e.title: e for e in result.extracts}
    assert "Haute-Saône" not in by_title  # its history has nothing for the period
    assert by_title["Histoire de la Haute-Saône"].level == "department"
    assert "1880" in by_title["Histoire de la Haute-Saône"].text
    assert "1200" not in by_title["Histoire de la Haute-Saône"].text
    assert not any("fusion" in e.text for e in result.extracts)
    assert "Belle Époque" in wiki.requested
