"""
Tests of the French administrative tables.
"""

###########
# Imports #
###########

# Local imports #
from storyteller import fr_admin

#############
# Functions #
#############


def test_postal_and_insee_codes_give_the_department():
    assert fr_admin.department_code_from_place_code("29880") == "29"
    assert fr_admin.department_code_from_place_code("29233") == "29"
    assert fr_admin.department_code_from_place_code("75056") == "75"
    assert fr_admin.department_code_from_place_code(" 01 000 ") == "01"


def test_overseas_and_corsica():
    assert fr_admin.department_code_from_place_code("97411") == "974"
    assert fr_admin.department_code_from_place_code("2A004") == "2A"
    assert fr_admin.department_code_from_place_code("2b033") == "2B"
    assert fr_admin.department_code_from_place_code("20000") == "2A"
    assert fr_admin.department_code_from_place_code("20200") == "2B"
    assert fr_admin.department_code_from_place_code("20200", "Corse-du-Sud") == "2A"


def test_invalid_codes_are_ignored():
    for code in (None, "", "ABCDE", "1234", "98000", "SW1A 1AA"):
        assert fr_admin.department_code_from_place_code(code) is None


def test_names_regions_and_provinces():
    assert fr_admin.department_name("29") == "Finistère"
    assert fr_admin.department_code_from_name("finistere") == "29"
    assert fr_admin.department_code_from_name("Côtes-du-Nord") == "22"
    assert fr_admin.region_name("29") == "Bretagne"
    assert fr_admin.region_name("29", "en") == "Brittany"
    assert fr_admin.province_name("14") == "Normandie"
    assert fr_admin.province_name("14", "en") == "Normandy"
    assert fr_admin.province_name("99") is None


def test_every_department_has_a_known_region_and_province():
    for code, (_name, region, province) in fr_admin.DEPARTMENTS.items():
        assert region in fr_admin.REGIONS, code
        assert province in fr_admin.PROVINCES_EN, code


def test_is_france():
    assert fr_admin.is_france("France")
    assert fr_admin.is_france("République française")
    assert not fr_admin.is_france("Belgique")
    assert not fr_admin.is_france(None)
