"""
API key storage.

Lookup order: environment variable, system keyring (if the keyring package is
available in Gramps' Python), then an INI file readable by the owner only.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import configparser
import os
import stat
from typing import Optional

# Local imports #
from .config import config_dir
from .llm.registry import PRESETS

#############
# Constants #
#############

KEYRING_SERVICE = "GrampsStoryteller"
GENERIC_ENV_VAR = "GRAMPSSTORYTELLER_API_KEY"

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

#############
# Functions #
#############


def credentials_file() -> str:
    """
    Return the path of the fallback credentials file.

    Returns
    -------
    str
        File path.
    """
    return os.path.join(config_dir(), "credentials.ini")


def env_var_name(provider_id: str) -> Optional[str]:
    """
    Return the environment variable of a provider.

    Parameters
    ----------
    provider_id : str
        Provider identifier.

    Returns
    -------
    str or None
        Variable name, None when the provider has none.
    """
    preset = PRESETS.get(provider_id)
    return preset.env_var if preset else None


def _read_file(provider_id: str) -> Optional[str]:
    """
    Read a key from the credentials file.

    Parameters
    ----------
    provider_id : str
        Provider identifier.

    Returns
    -------
    str or None
        Key, None when missing.
    """
    parser = configparser.RawConfigParser()
    try:
        parser.read(credentials_file(), encoding="utf-8")
        return parser.get(provider_id, "api_key") or None
    except (configparser.Error, OSError):
        return None


def _write_file(provider_id: str, api_key: Optional[str]):
    """
    Write (or remove) a key in the credentials file, with 600 permissions.

    Parameters
    ----------
    provider_id : str
        Provider identifier.
    api_key : str or None
        Key, None to remove it.
    """
    path = credentials_file()
    parser = configparser.RawConfigParser()
    parser.read(path, encoding="utf-8")
    if api_key:
        if not parser.has_section(provider_id):
            parser.add_section(provider_id)
        parser.set(provider_id, "api_key", api_key)
    elif parser.has_section(provider_id):
        parser.remove_section(provider_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        parser.write(file)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def key_source(provider_id: str) -> Optional[str]:
    """
    Tell where the key of a provider comes from.

    Parameters
    ----------
    provider_id : str
        Provider identifier.

    Returns
    -------
    str or None
        "env", "keyring", "file" or None when no key is stored.
    """
    variable = env_var_name(provider_id)
    if (variable and os.environ.get(variable)) or os.environ.get(GENERIC_ENV_VAR):
        return "env"
    if HAS_KEYRING:
        try:
            if keyring.get_password(KEYRING_SERVICE, provider_id):
                return "keyring"
        except Exception:  # noqa: BLE001 - keyring backends raise various errors
            pass
    if _read_file(provider_id):
        return "file"
    return None


def get_api_key(provider_id: str) -> Optional[str]:
    """
    Return the API key of a provider.

    Parameters
    ----------
    provider_id : str
        Provider identifier.

    Returns
    -------
    str or None
        Key, None when not configured.
    """
    variable = env_var_name(provider_id)
    if variable and os.environ.get(variable):
        return os.environ[variable]
    if os.environ.get(GENERIC_ENV_VAR):
        return os.environ[GENERIC_ENV_VAR]
    if HAS_KEYRING:
        try:
            value = keyring.get_password(KEYRING_SERVICE, provider_id)
            if value:
                return value
        except Exception:  # noqa: BLE001 - keyring backends raise various errors
            pass
    return _read_file(provider_id)


def set_api_key(provider_id: str, api_key: Optional[str]) -> str:
    """
    Store (or delete when empty) the API key of a provider.

    Parameters
    ----------
    provider_id : str
        Provider identifier.
    api_key : str or None
        Key.

    Returns
    -------
    str
        Storage used: "keyring" or "file".
    """
    api_key = (api_key or "").strip() or None
    if HAS_KEYRING:
        try:
            if api_key:
                keyring.set_password(KEYRING_SERVICE, provider_id, api_key)
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE, provider_id)
                except Exception:  # noqa: BLE001 - keyring backends raise various errors
                    pass
            # Never keep a stale copy in the file
            if _read_file(provider_id):
                _write_file(provider_id, None)
            return "keyring"
        except Exception:  # noqa: BLE001 - keyring backends raise various errors
            pass
    _write_file(provider_id, api_key)
    return "file"
