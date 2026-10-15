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
