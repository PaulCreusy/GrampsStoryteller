"""
Shared fixtures of the test suite.
"""

###########
# Imports #
###########

# Python imports #
import os
import sys

# Dependencies #
import pytest

#############
# Constants #
#############

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Local imports #
from storyteller.models import PlaceInfo  # noqa: E402
from storyteller.sources.wikipedia import Page  # noqa: E402

###########
# Classes #
###########


class FakeWikipedia:
    """
    In-memory stand-in for WikipediaClient.

    Parameters
    ----------
    pages : dict
        Title -> plain-text extract.
    search_results : dict, optional
        Query -> titles.
    geo_results : list of str, optional
        Titles returned by any geosearch.
    lang : str
        Language code.
    disambiguation : set of str, optional
        Titles of disambiguation pages.
    """

    def __init__(self, pages, search_results=None, geo_results=None, lang="fr",
                 disambiguation=None):
        self.pages = pages
        self.search_results = search_results or {}
        self.geo_results = geo_results or []
        self.lang = lang
        self.disambiguation = disambiguation or set()
        self.requested = []

    def get_page(self, title):
        self.requested.append(title)
        if title not in self.pages:
            return None
        return Page(title=title, url="https://%s.wikipedia.org/w/index.php?title=%s&oldid=1"
                    % (self.lang, title.replace(" ", "_")),
                    text=self.pages[title], disambiguation=title in self.disambiguation)

    def search(self, query, limit=5):
        self.requested.append("search:" + query)
        return self.search_results.get(query, [])[:limit]

    def geosearch(self, latitude, longitude, radius=10000, limit=10):
        self.requested.append("geo")
        return self.geo_results[:limit]


############
# Fixtures #
############


@pytest.fixture
def brest():
    """
    Return a French place with a postal code.

    Returns
    -------
    PlaceInfo
        Place.
    """
    return PlaceInfo(name="Plouguerneau", country="France", code="29880")


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """
    Point the configuration and cache directories to a temporary folder.

    Returns
    -------
    pathlib.Path
        Temporary folder.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                     "GRAMPSSTORYTELLER_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    import storyteller.credentials as credentials
    monkeypatch.setattr(credentials, "HAS_KEYRING", False)
    return tmp_path
