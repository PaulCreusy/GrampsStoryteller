#!/usr/bin/python3
"""
Build the Gramps addon archive and the listings that let Gramps' Addon
Manager install it from this repository.

Output (committed, then served through raw.githubusercontent.com), for each
supported Gramps version (5.1, 5.2 and 6.0):
    addons/gramps<MAJOR><MINOR>/download/GrampsStoryteller.addon.tgz
    addons/gramps<MAJOR><MINOR>/listings/addons-<lang>.json  (5.2 and later)
    addons/gramps<MAJOR><MINOR>/listings/addons-<lang>.txt   (5.1)

The translations (po/<lang>-local.po) are compiled into the archive as
locale/<lang>/LC_MESSAGES/addon.mo, and the listing of a translated
language carries the translated name and description.

The archive is reproducible (fixed owners, dates and order) so that the CI
can check that the committed files match the sources.

Usage:
    python3 scripts/build_addon.py [--gramps-version 6.0]

Without --gramps-version, every supported version is built.
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

# Local imports #
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import i18n  # noqa: E402

#############
# Constants #
#############

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = "GrampsStoryteller"
GPR_FILE = "GrampsStoryteller.gpr.py"

TOP_LEVEL_FILES = [GPR_FILE, "GrampsStoryteller.py", "README.md", "LICENSE"]
PACKAGE_DIRS = ["storyteller"]
PACKAGE_EXTENSIONS = (".py", ".txt")

# Supported Gramps versions. The archive is the same for all of them: the
# .gpr.py reads the running version
GRAMPS_VERSIONS = ("5.1", "5.2", "6.0")
# Gramps 5.1 reads one Python dict per line from listings/addons-<lang>.txt
TXT_LISTING_VERSIONS = ("5.1",)

# Gramps' PTYPE integer for a tool (gramps.gen.plug.TOOL == 2)
TOOL_PTYPE = 2
# Gramps 5.1 identifies the type by its (untranslated) name
TOOL_PTYPE_NAME = "Tool"
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


def read_gpr_metadata(gramps_version: str = "6.0") -> dict:
    """
    Execute the .gpr.py with a stub register() to read its metadata.

    Parameters
    ----------
    gramps_version : str
        Gramps major.minor seen by the .gpr.py.

    Returns
    -------
    dict
        Keyword arguments given to register().
    """
    captured = {}

    def register(ptype, **kwargs):
        captured.update(kwargs)

    version_module = types.ModuleType("gramps.version")
    version_module.major_version = gramps_version
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


def compiled_translations() -> dict:
    """
    Compile the translations of the addon.

    Returns
    -------
    dict
        Path inside the addon directory -> content of the .mo file.
    """
    return {"locale/%s/LC_MESSAGES/addon.mo" % lang: i18n.compile_mo(i18n.read_po(path))
            for lang, path in i18n.po_files().items()}


def translate_listing(lang: str, meta: dict) -> dict:
    """
    Return the name and description of the addon in a language.

    Parameters
    ----------
    lang : str
        Listing language (e.g. "fr" or "pt_BR").
    meta : dict
        Metadata of the .gpr.py.

    Returns
    -------
    dict
        Name ("n") and description ("d").
    """
    path = i18n.po_files().get(lang) or i18n.po_files().get(lang.split("_")[0])
    catalog = {}
    if path:
        catalog = {e.msgid: e.msgstr for e in i18n.read_po(path)
                   if e.msgstr and not e.obsolete and "fuzzy" not in e.flags}
    return {"n": catalog.get(meta["name"], meta["name"]),
            "d": catalog.get(meta["description"], meta["description"])}


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
        for member, content in sorted(compiled_translations().items()):
            info = reset(tarfile.TarInfo("%s/%s" % (ADDON_DIR, member)))
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(buffer.getvalue())


def listing_entry(lang: str, meta: dict, gramps_version: str) -> dict:
    """
    Return the listing entry of the addon for a language and a Gramps version.

    Parameters
    ----------
    lang : str
        Listing language.
    meta : dict
        Metadata of the .gpr.py, read for this Gramps version.
    gramps_version : str
        Gramps major.minor.

    Returns
    -------
    dict
        Entry, in the format the Addon Manager of this version expects.
    """
    texts = translate_listing(lang, meta)
    if gramps_version in TXT_LISTING_VERSIONS:
        return {"t": TOOL_PTYPE_NAME, "i": meta["id"], "n": texts["n"], "v": meta["version"],
                "g": gramps_version, "d": texts["d"], "z": "%s.addon.tgz" % ADDON_DIR}
    entry = {
        "t": TOOL_PTYPE,
        "i": meta["id"],
        "n": texts["n"],
        "v": meta["version"],
        "g": gramps_version,
        "d": texts["d"],
        "z": "%s.addon.tgz" % ADDON_DIR,
        "s": STATUS_STABLE,
        "a": AUDIENCE_EVERYONE,
    }
    if meta.get("help_url"):
        # Shown as a help button in the Addon Manager
        entry["h"] = meta["help_url"]
    return entry


def build_version(gramps_version: str) -> str:
    """
    Build the archive and the listings of one Gramps version.

    Parameters
    ----------
    gramps_version : str
        Gramps major.minor.

    Returns
    -------
    str
        Output directory.
    """
    major, minor = gramps_version.split(".")[:2]
    out_dir = os.path.join(REPO_ROOT, "addons", "gramps%s%s" % (major, minor))
    download_dir = os.path.join(out_dir, "download")
    listings_dir = os.path.join(out_dir, "listings")
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(listings_dir, exist_ok=True)

    meta = read_gpr_metadata(gramps_version)
    build_archive(os.path.join(download_dir, "%s.addon.tgz" % ADDON_DIR))

    for lang in LANGUAGES:
        entry = listing_entry(lang, meta, gramps_version)
        if gramps_version in TXT_LISTING_VERSIONS:
            path = os.path.join(listings_dir, "addons-%s.txt" % lang)
            content = repr(entry) + "\n"
        else:
            path = os.path.join(listings_dir, "addons-%s.json" % lang)
            content = json.dumps([entry], indent=2, ensure_ascii=False) + "\n"
        with open(path, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
    return out_dir


def main(argv=None):
    """
    Build the archives and the listings.

    Parameters
    ----------
    argv : list of str, optional
        Command line arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gramps-version", choices=GRAMPS_VERSIONS,
                        help="Gramps major.minor to build (default: all)")
    args = parser.parse_args(argv)

    for gramps_version in [args.gramps_version] if args.gramps_version else GRAMPS_VERSIONS:
        out_dir = build_version(gramps_version)
        print("Built %s.addon.tgz and the listings in %s" % (ADDON_DIR, out_dir))


#############
# Execution #
#############

if __name__ == "__main__":
    main()
