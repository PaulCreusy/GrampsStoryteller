"""
Tests of the translations of the interface.
"""

###########
# Imports #
###########

# Python imports #
import gettext
import importlib.util
import io
import os
import re

# Local imports #
from conftest import REPO_ROOT

#############
# Constants #
#############

_PLACEHOLDER = re.compile(r"%(?:\([a-z_]+\))?[sd]")
_VARIABLE = re.compile(r"\{[a-z_]+\}")

#############
# Functions #
#############


def _load(name):
    path = os.path.join(REPO_ROOT, "scripts", "%s.py" % name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_message_is_translated_in_french():
    i18n = _load("i18n")
    assert "fr" in i18n.po_files()
    assert i18n.missing_translations("fr") == []


def test_placeholders_and_variables_are_kept():
    i18n = _load("i18n")
    for entry in i18n.read_po(i18n.po_files()["fr"]):
        if not entry.msgid or entry.obsolete:
            continue
        assert sorted(_PLACEHOLDER.findall(entry.msgid)) == \
            sorted(_PLACEHOLDER.findall(entry.msgstr)), entry.msgid
        assert sorted(_VARIABLE.findall(entry.msgid)) == \
            sorted(_VARIABLE.findall(entry.msgstr)), entry.msgid
        if entry.msgid.startswith("_"):
            # Button mnemonic
            assert entry.msgstr.count("_") == 1, entry.msgid


def test_po_round_trip_and_compiled_catalog(tmp_path):
    i18n = _load("i18n")
    path = tmp_path / "xx-local.po"
    entries = [
        i18n.Entry(msgid="Hello", msgstr="Bonjour"),
        i18n.Entry(msgid='Say "%s"\nnow', msgstr='Dites « %s »\nmaintenant',
                   references=["a.py:1"], flags=["python-format"]),
        i18n.Entry(msgid="Fuzzy", msgstr="Flou", flags=["fuzzy"]),
        i18n.Entry(msgid="Old", msgstr="Ancien", obsolete=True),
    ]
    i18n.write_po(str(path), entries, "xx")
    read = [e for e in i18n.read_po(str(path)) if e.msgid]
    assert [(e.msgid, e.msgstr, e.obsolete) for e in read] == \
        [(e.msgid, e.msgstr, e.obsolete) for e in entries]

    catalog = gettext.GNUTranslations(io.BytesIO(i18n.compile_mo(i18n.read_po(str(path)))))
    assert catalog.gettext("Hello") == "Bonjour"
    assert catalog.gettext('Say "%s"\nnow') == "Dites « %s »\nmaintenant"
    assert catalog.gettext("Fuzzy") == "Fuzzy"
    assert catalog.gettext("Old") == "Old"


def test_archive_ships_the_french_catalog(tmp_path):
    builder = _load("build_addon")
    translations = builder.compiled_translations()
    content = translations["locale/fr/LC_MESSAGES/addon.mo"]
    catalog = gettext.GNUTranslations(io.BytesIO(content))
    assert catalog.gettext("Whole tree") == "Tout l'arbre"
    meta = builder.read_gpr_metadata()
    assert builder.translate_listing("fr", meta)["n"] == "Storyteller : contexte historique"
    assert builder.translate_listing("de", meta)["n"] == meta["name"]
