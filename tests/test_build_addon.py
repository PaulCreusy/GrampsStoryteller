"""
Tests of the addon packaging.
"""

###########
# Imports #
###########

# Python imports #
import hashlib
import importlib.util
import os
import tarfile

# Local imports #
from conftest import REPO_ROOT

#############
# Functions #
#############


def _load_builder():
    path = os.path.join(REPO_ROOT, "scripts", "build_addon.py")
    spec = importlib.util.spec_from_file_location("build_addon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_and_members():
    builder = _load_builder()
    meta = builder.read_gpr_metadata()
    assert meta["id"] == "gramps_storyteller"
    assert meta["toolclass"] == "StorytellerTool"
    from storyteller import __version__
    assert meta["version"] == __version__
    members = builder.archive_members()
    assert "GrampsStoryteller.gpr.py" in members
    assert "storyteller/prompts/system_fr.txt" in members
    assert "storyteller/gui/tool_window.py" in members
    assert "storyteller/prompts/correction_fr.txt" in members
    assert not any("__pycache__" in m or m.startswith("tests") for m in members)


def test_archive_is_reproducible(tmp_path):
    builder = _load_builder()
    first, second = tmp_path / "a.tgz", tmp_path / "b.tgz"
    builder.build_archive(str(first))
    builder.build_archive(str(second))
    assert hashlib.sha256(first.read_bytes()).digest() == \
        hashlib.sha256(second.read_bytes()).digest()
    with tarfile.open(first) as tar:
        names = tar.getnames()
    assert all(name.startswith("GrampsStoryteller/") for name in names)


def test_help_url_is_not_registered_with_gramps_5_1():
    builder = _load_builder()
    assert "help_url" not in builder.read_gpr_metadata("5.1")
    for version in ("5.2", "6.0"):
        assert builder.read_gpr_metadata(version)["help_url"].startswith("https://github.com/")


def test_listing_format_of_each_gramps_version():
    builder = _load_builder()
    old = builder.listing_entry("fr", builder.read_gpr_metadata("5.1"), "5.1")
    assert old["t"] == "Tool" and old["g"] == "5.1"
    assert old["n"] == "Storyteller : contexte historique"
    # Gramps 5.1 evaluates each line of the .txt listing as a Python literal
    assert eval(repr(old), {}) == old
    new = builder.listing_entry("en", builder.read_gpr_metadata("6.0"), "6.0")
    assert (new["t"], new["s"], new["a"], new["g"]) == (2, 3, 0, "6.0")
    assert new["h"].startswith("https://github.com/")


def test_committed_listings_exist_for_every_version():
    builder = _load_builder()
    for version in builder.GRAMPS_VERSIONS:
        folder = os.path.join(REPO_ROOT, "addons", "gramps" + version.replace(".", ""))
        extension = "txt" if version in builder.TXT_LISTING_VERSIONS else "json"
        assert os.path.exists(os.path.join(folder, "listings", "addons-en.%s" % extension))
        assert os.path.exists(os.path.join(folder, "download", "GrampsStoryteller.addon.tgz"))
