"""
User settings, stored as JSON in the user configuration directory.

The API keys are not stored here (see credentials.py).
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Dict, Optional

# Local imports #
from .llm.registry import PRESETS

#############
# Constants #
#############

APP_DIR_NAME = "grampsstoryteller"

DEFAULTS: Dict[str, Any] = {
    "provider": "openai",
    # provider id -> {"base_url": str, "model": str}
    "providers": {},
    "temperature": 0.3,
    "send_temperature": True,
    "max_tokens": 8000,
    "language": "fr",
    "length_words": 250,
    "include_occupation": False,
    "llm_validation": False,
    # Rewrite the text when the LLM validation finds problems
    "auto_correction": False,
    # None means "use the default prompt of the language"
    "system_prompt": None,
    "user_prompt": None,
    "validation_prompt": None,
    "correction_prompt": None,
}

#############
# Functions #
#############


def config_dir() -> str:
    """
    Return the configuration directory of the addon.

    Returns
    -------
    str
        Directory path.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_DIR_NAME)


def cache_dir() -> str:
    """
    Return the cache directory of the addon.

    Returns
    -------
    str
        Directory path.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_DIR_NAME, "cache")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, APP_DIR_NAME)


###########
# Classes #
###########


class Settings:
    """
    Settings with defaults, loaded from and saved to a JSON file.

    Parameters
    ----------
    path : str, optional
        Settings file. Defaults to settings.json in config_dir().
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(config_dir(), "settings.json")
        self.values: Dict[str, Any] = copy.deepcopy(DEFAULTS)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Settings":
        """
        Load the settings, ignoring a missing or corrupted file.

        Parameters
        ----------
        path : str, optional
            Settings file.

        Returns
        -------
        Settings
            Loaded settings.
        """
        settings = cls(path)
        try:
            with open(settings.path, encoding="utf-8") as file:
                stored = json.load(file)
            if isinstance(stored, dict):
                for key in DEFAULTS:
                    if key in stored:
                        settings.values[key] = stored[key]
        except (OSError, ValueError):
            pass
        return settings

    def save(self):
        """
        Write the settings file.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(self.values, file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __setitem__(self, key: str, value: Any):
        self.values[key] = value

    def provider_setting(self, provider_id: str, key: str) -> str:
        """
        Return the base URL or model of a provider, with the preset default.

        Parameters
        ----------
        provider_id : str
            Provider identifier.
        key : str
            "base_url" or "model".

        Returns
        -------
        str
            Value.
        """
        stored = self.values["providers"].get(provider_id, {}).get(key)
        if stored:
            return stored
        preset = PRESETS.get(provider_id)
        if preset is None:
            return ""
        return preset.base_url if key == "base_url" else preset.default_model

    def set_provider_setting(self, provider_id: str, key: str, value: str):
        """
        Store the base URL or model of a provider.

        Parameters
        ----------
        provider_id : str
            Provider identifier.
        key : str
            "base_url" or "model".
        value : str
            Value.
        """
        self.values["providers"].setdefault(provider_id, {})[key] = value.strip()
