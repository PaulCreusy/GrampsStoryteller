"""
Small JSON disk cache for Wikipedia pages and generated texts.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

#############
# Constants #
#############

DEFAULT_TTL = 30 * 24 * 3600

###########
# Classes #
###########


class DiskCache:
    """
    Key/value store of JSON documents, one file per key.

    Parameters
    ----------
    directory : str or None
        Cache directory. None disables the cache.
    ttl : float
        Lifetime of an entry, in seconds.
    """

    def __init__(self, directory: Optional[str], ttl: float = DEFAULT_TTL):
        self.directory = directory
        self.ttl = ttl
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _path(self, namespace: str, key: str) -> str:
        """
        Return the file path of an entry.

        Parameters
        ----------
        namespace : str
            Entry family ("wiki", "llm"...).
        key : str
            Entry key.

        Returns
        -------
        str
            File path.
        """
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, "%s-%s.json" % (namespace, digest[:40]))

    def get(self, namespace: str, key: str) -> Optional[object]:
        """
        Read an entry.

        Parameters
        ----------
        namespace : str
            Entry family.
        key : str
            Entry key.

        Returns
        -------
        object or None
            Stored value, None when missing or expired.
        """
        if not self.directory:
            return None
        path = self._path(namespace, key)
        try:
            if time.time() - os.path.getmtime(path) > self.ttl:
                return None
            with open(path, encoding="utf-8") as file:
                return json.load(file)["value"]
        except (OSError, ValueError, KeyError):
            return None

    def set(self, namespace: str, key: str, value: object):
        """
        Write an entry.

        Parameters
        ----------
        namespace : str
            Entry family.
        key : str
            Entry key.
        value : object
            JSON-serializable value.
        """
        if not self.directory:
            return
        path = self._path(namespace, key)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump({"key": key, "value": value}, file, ensure_ascii=False)
            os.replace(tmp_path, path)
        except OSError:
            pass

    def clear(self) -> int:
        """
        Delete every entry.

        Returns
        -------
        int
            Number of deleted files.
        """
        if not self.directory or not os.path.isdir(self.directory):
            return 0
        count = 0
        for name in os.listdir(self.directory):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(self.directory, name))
                    count += 1
                except OSError:
                    pass
        return count
