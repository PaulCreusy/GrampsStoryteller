#!/usr/bin/python3
"""
Build the Gramps addon archive and the listings that let Gramps' Addon
Manager install it from this repository.

Output (committed, then served through raw.githubusercontent.com):
    addons/gramps<MAJOR><MINOR>/download/GrampsStoryteller.addon.tgz
    addons/gramps<MAJOR><MINOR>/listings/addons-<lang>.json

The archive is reproducible (fixed owners, dates and order) so that the CI
can check that the committed files match the sources.

Usage:
    python3 scripts/build_addon.py [--gramps-version 6.0]
"""

###########
# Imports #
###########

# Python imports #
import argparse
import gzip
import io
import json
import os
import sys
import tarfile
import types

#############
# Constants #
#############

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = "GrampsStoryteller"
GPR_FILE = "GrampsStoryteller.gpr.py"

TOP_LEVEL_FILES = [GPR_FILE, "GrampsStoryteller.py", "README.md", "LICENSE"]
PACKAGE_DIRS = ["storyteller"]
PACKAGE_EXTENSIONS = (".py", ".txt")

# Gramps' PTYPE integer for a tool (gramps.gen.plug.TOOL == 2)
TOOL_PTYPE = 2
# The Addon Manager hides, by default, every addon that is not "Stable"
# (s == 3) and "Everyone" (a == 0)
STATUS_STABLE = 3
AUDIENCE_EVERYONE = 0
# Gramps asks for listings/addons-<lang>.json for the languages of the user
# locale, then "en": the same listing is served for every language
LANGUAGES = (
    "ar ba bg ca cs da de de_AT el en en_GB eo es fi fr ga he hr hu is it ja "
    "ko lt nb nl nn pl pt pt_BR pt_PT ru sk sl sq sr sv ta tr uk vi zh zh_CN "
    "zh_HK zh_TW"
).split()

#############
# Functions #
#############


def read_gpr_metadata() -> dict:
    """
    Execute the .gpr.py with a stub register() to read its metadata.

    Returns
    -------
    dict
        Keyword arguments given to register().
    """
    captured = {}

    def register(ptype, **kwargs):
        captured.update(kwargs)

    version_module = types.ModuleType("gramps.version")
    version_module.major_version = "0.0"
    saved = {name: sys.modules.get(name) for name in ("gramps", "gramps.version")}
    sys.modules["gramps"] = types.ModuleType("gramps")
    sys.modules["gramps.version"] = version_module

    env = {
        "register": register, "_": lambda s: s, "TOOL": "Tool", "STABLE": "Stable",
        "TOOL_DBPROC": 0, "TOOL_MODE_GUI": 0,
    }
    try:
        with open(os.path.join(REPO_ROOT, GPR_FILE), encoding="utf-8") as file:
            exec(compile(file.read(), GPR_FILE, "exec"), env)
    finally:
        # Do not leak the stubs (the real Gramps may be imported later)
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return captured


def archive_members() -> list:
    """
    List the files shipped in the archive, relative to the repository root.

    Returns
    -------
    list of str
        Sorted paths.
    """
    members = list(TOP_LEVEL_FILES)
    for package in PACKAGE_DIRS:
        for root, dirs, files in os.walk(os.path.join(REPO_ROOT, package)):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith(PACKAGE_EXTENSIONS):
                    members.append(os.path.relpath(os.path.join(root, name), REPO_ROOT))
    return [m.replace(os.sep, "/") for m in members]


def build_archive(path: str):
    """
    Write a reproducible .tgz of the addon.

    Parameters
    ----------
    path : str
        Output file.
    """
    def reset(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uname = info.gname = "gramps"
        info.uid = info.gid = 0
        info.mtime = 0
        info.mode = 0o644
        return info

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for member in archive_members():
            tar.add(os.path.join(REPO_ROOT, member), arcname="%s/%s" % (ADDON_DIR, member),
                    filter=reset)
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(buffer.getvalue())


def main(argv=None):
    """
    Build the archive and the listings.

    Parameters
    ----------
    argv : list of str, optional
        Command line arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gramps-version", default="6.0",
                        help="Gramps major.minor the package targets (default: 6.0)")
    args = parser.parse_args(argv)

    major, minor = args.gramps_version.split(".")[:2]
    out_dir = os.path.join(REPO_ROOT, "addons", "gramps%s%s" % (major, minor))
    download_dir = os.path.join(out_dir, "download")
    listings_dir = os.path.join(out_dir, "listings")
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(listings_dir, exist_ok=True)

    meta = read_gpr_metadata()
    tgz_name = "%s.addon.tgz" % ADDON_DIR
    build_archive(os.path.join(download_dir, tgz_name))

    listing = [{
        "t": TOOL_PTYPE,
        "i": meta["id"],
        "n": meta["name"],
        "v": meta["version"],
        "g": args.gramps_version,
        "d": meta["description"],
        "z": tgz_name,
        "s": STATUS_STABLE,
        "a": AUDIENCE_EVERYONE,
    }]
    for lang in LANGUAGES:
        with open(os.path.join(listings_dir, "addons-%s.json" % lang), "w",
                  encoding="utf-8", newline="\n") as file:
            json.dump(listing, file, indent=2, ensure_ascii=False)
            file.write("\n")

    print("Built %s and the listings in %s" % (tgz_name, out_dir))


#############
# Execution #
#############

if __name__ == "__main__":
    main()
