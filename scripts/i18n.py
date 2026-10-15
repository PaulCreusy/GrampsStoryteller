#!/usr/bin/python3
"""
Translation tools of the addon, without any dependency on GNU gettext.

The interface strings are the literals passed to _() in the .gpr.py and in
the storyteller package. They are extracted to po/template.pot and merged
into the po/<lang>-local.po files (Gramps addon convention). The build
script compiles these files to locale/<lang>/LC_MESSAGES/addon.mo inside
the archive.

Usage:
    python3 scripts/i18n.py update   # refresh template.pot and the .po files
    python3 scripts/i18n.py check    # list the missing translations
"""

###########
# Imports #
###########

# Python imports #
import argparse
import ast
import glob
import os
import struct
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

#############
# Constants #
#############

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PO_DIR = os.path.join(REPO_ROOT, "po")
TEMPLATE = os.path.join(PO_DIR, "template.pot")
SOURCES = ["GrampsStoryteller.gpr.py", "storyteller/**/*.py"]

HEADER = (
    "Project-Id-Version: GrampsStoryteller\n"
    "Language: {lang}\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
)

###########
# Classes #
###########


@dataclass
class Entry:
    """
    One message of a .po file.

    Attributes
    ----------
    msgid : str
        Original (English) message.
    msgstr : str
        Translation, empty when missing.
    references : list of str
        Source locations ("file:line").
    flags : list of str
        Flags such as "fuzzy" or "python-format".
    obsolete : bool
        Whether the message no longer exists in the sources.
    """

    msgid: str
    msgstr: str = ""
    references: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    obsolete: bool = False


#############
# Functions #
#############


def source_files() -> List[str]:
    """
    List the files whose _() strings are translated.

    Returns
    -------
    list of str
        Paths relative to the repository root.
    """
    files = []
    for pattern in SOURCES:
        for path in sorted(glob.glob(os.path.join(REPO_ROOT, pattern), recursive=True)):
            files.append(os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"))
    return files


def extract_messages() -> "OrderedDict[str, List[str]]":
    """
    Extract the literal strings passed to _().

    Returns
    -------
    OrderedDict
        Message -> source locations, in source order.

    Raises
    ------
    ValueError
        When _() is called with something else than a literal string.
    """
    messages: "OrderedDict[str, List[str]]" = OrderedDict()
    for path in source_files():
        with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as file:
            tree = ast.parse(file.read(), path)
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "_" and node.args]
        for node in sorted(calls, key=lambda n: (n.lineno, n.col_offset)):
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                raise ValueError("%s:%d: _() needs a literal string" % (path, node.lineno))
            messages.setdefault(arg.value, []).append("%s:%d" % (path, node.lineno))
    return messages


def _is_format(message: str) -> bool:
    """
    Tell whether a message contains Python % placeholders.

    Parameters
    ----------
    message : str
        Message.

    Returns
    -------
    bool
        True when it does.
    """
    return "%" in message.replace("%%", "")


def _unquote(text: str) -> str:
    """
    Decode a quoted .po string.

    Parameters
    ----------
    text : str
        String with its quotes.

    Returns
    -------
    str
        Decoded value.
    """
    return ast.literal_eval(text.strip())


def _quote(value: str) -> str:
    """
    Encode a string for a .po file, one line per message line.

    Parameters
    ----------
    value : str
        Value.

    Returns
    -------
    str
        One or several quoted lines.
    """
    def escape(part: str) -> str:
        return '"%s"' % (part.replace("\\", "\\\\").replace('"', '\\"')
                         .replace("\n", "\\n").replace("\t", "\\t"))

    if "\n" not in value.rstrip("\n"):
        return escape(value)
    parts = value.splitlines(keepends=True)
    return '""\n' + "\n".join(escape(part) for part in parts)


def read_po(path: str) -> List[Entry]:
    """
    Read a .po file (without plural forms).

    Parameters
    ----------
    path : str
        File path.

    Returns
    -------
    list of Entry
        Entries, the header included (empty msgid).
    """
    entries: List[Entry] = []
    current: Optional[Entry] = None
    target = None

    def finish():
        if current is not None and target is not None:
            entries.append(current)

    with open(path, encoding="utf-8") as file:
        lines = file.read().splitlines()
    for raw in lines + [""]:
        line = raw.strip()
        obsolete = line.startswith("#~")
        if obsolete:
            line = line[2:].strip()
        if not line:
            finish()
            current, target = None, None
            continue
        if current is None:
            current = Entry(msgid="")
        if line.startswith("#,"):
            current.flags.extend(f.strip() for f in line[2:].split(",") if f.strip())
        elif line.startswith("#:"):
            current.references.extend(line[2:].split())
        elif line.startswith("#"):
            continue
        elif line.startswith("msgid "):
            if target == "msgstr":
                # Entries not separated by a blank line
                finish()
                current = Entry(msgid="")
            current.obsolete = obsolete
            current.msgid = _unquote(line[6:])
            target = "msgid"
        elif line.startswith("msgstr "):
            current.msgstr = _unquote(line[7:])
            target = "msgstr"
        elif line.startswith('"'):
            setattr(current, target, getattr(current, target) + _unquote(line))
    return entries


def write_po(path: str, entries: List[Entry], lang: str = ""):
    """
    Write a .po or .pot file.

    Parameters
    ----------
    path : str
        File path.
    entries : list of Entry
        Entries without the header.
    lang : str
        Language code, empty for the template.
    """
    blocks = ['msgid ""\nmsgstr %s' % _quote(HEADER.format(lang=lang))]
    for entry in entries:
        lines = []
        prefix = "#~ " if entry.obsolete else ""
        if entry.references and not entry.obsolete:
            lines.append("#: " + " ".join(entry.references))
        if entry.flags:
            lines.append("#, " + ", ".join(entry.flags))
        for keyword, value in (("msgid", entry.msgid), ("msgstr", entry.msgstr)):
            quoted = _quote(value).split("\n")
            lines.append("%s%s %s" % (prefix, keyword, quoted[0]))
            lines.extend(prefix + part for part in quoted[1:])
        blocks.append("\n".join(lines))
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n\n".join(blocks) + "\n")


def po_files() -> Dict[str, str]:
    """
    List the translations.

    Returns
    -------
    dict
        Language code -> .po path.
    """
    result = {}
    for path in sorted(glob.glob(os.path.join(PO_DIR, "*-local.po"))):
        result[os.path.basename(path)[:-len("-local.po")]] = path
    return result


def merge(messages: "OrderedDict[str, List[str]]", existing: List[Entry]) -> List[Entry]:
    """
    Merge the extracted messages with the translations of a .po file.

    Parameters
    ----------
    messages : OrderedDict
        Extracted messages and their locations.
    existing : list of Entry
        Entries of the .po file.

    Returns
    -------
    list of Entry
        Entries in source order, then the obsolete translations.
    """
    known = {entry.msgid: entry for entry in existing if entry.msgid}
    merged = []
    for msgid, references in messages.items():
        old = known.pop(msgid, None)
        flags = ["python-format"] if _is_format(msgid) else []
        if old is not None and "fuzzy" in old.flags:
            flags.insert(0, "fuzzy")
        merged.append(Entry(msgid=msgid, msgstr=old.msgstr if old else "",
                            references=references, flags=flags))
    for old in known.values():
        if old.msgstr:
            merged.append(Entry(msgid=old.msgid, msgstr=old.msgstr, obsolete=True))
    return merged


def update():
    """
    Refresh the template and merge it into every translation.
    """
    messages = extract_messages()
    template = [Entry(msgid=m, references=r, flags=["python-format"] if _is_format(m) else [])
                for m, r in messages.items()]
    os.makedirs(PO_DIR, exist_ok=True)
    write_po(TEMPLATE, template)
    for lang, path in po_files().items():
        write_po(path, merge(messages, read_po(path)), lang)
        print("Updated %s" % os.path.relpath(path, REPO_ROOT))


def missing_translations(lang: str) -> List[str]:
    """
    List the messages of the sources without a usable translation.

    Parameters
    ----------
    lang : str
        Language code.

    Returns
    -------
    list of str
        Untranslated or fuzzy messages.
    """
    entries = {e.msgid: e for e in read_po(po_files()[lang]) if not e.obsolete}
    missing = []
    for msgid in extract_messages():
        entry = entries.get(msgid)
        if entry is None or not entry.msgstr or "fuzzy" in entry.flags:
            missing.append(msgid)
    return missing


def compile_mo(entries: List[Entry]) -> bytes:
    """
    Compile entries to the GNU .mo format.

    Fuzzy, obsolete and untranslated entries are left out, so that Gramps
    falls back to the English message.

    Parameters
    ----------
    entries : list of Entry
        Entries, the header included.

    Returns
    -------
    bytes
        Content of the .mo file.
    """
    catalog = {}
    for entry in entries:
        if entry.obsolete or "fuzzy" in entry.flags or not entry.msgstr:
            continue
        catalog[entry.msgid.encode("utf-8")] = entry.msgstr.encode("utf-8")
    keys = sorted(catalog)
    ids = b"".join(key + b"\0" for key in keys)
    strs = b"".join(catalog[key] + b"\0" for key in keys)
    count = len(keys)
    # Header (7 words), then the original and translated string tables
    ids_start = 7 * 4 + 16 * count
    strs_start = ids_start + len(ids)
    offsets_ids, offsets_strs = [], []
    position = 0
    for key in keys:
        offsets_ids += [len(key), ids_start + position]
        position += len(key) + 1
    position = 0
    for key in keys:
        value = catalog[key]
        offsets_strs += [len(value), strs_start + position]
        position += len(value) + 1
    header = struct.pack("Iiiiiii", 0x950412de, 0, count, 7 * 4, 7 * 4 + 8 * count, 0, 0)
    tables = struct.pack("%di" % (2 * count), *offsets_ids) + \
        struct.pack("%di" % (2 * count), *offsets_strs)
    return header + tables + ids + strs


def main(argv=None) -> int:
    """
    Run a translation command.

    Parameters
    ----------
    argv : list of str, optional
        Command line arguments.

    Returns
    -------
    int
        Exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("update", "check"))
    args = parser.parse_args(argv)
    if args.command == "update":
        update()
        return 0
    status = 0
    for lang in po_files():
        missing = missing_translations(lang)
        for msgid in missing:
            print("%s: missing translation: %r" % (lang, msgid))
        status = status or int(bool(missing))
    return status


#############
# Execution #
#############

if __name__ == "__main__":
    sys.exit(main())
