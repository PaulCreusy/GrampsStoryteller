"""
Read-only client of the MediaWiki API of Wikipedia.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable, List, Optional

# Local imports #
from ..cache import DiskCache
from ..http import build_url, request_json

#############
# Constants #
#############

API_URL = "https://%s.wikipedia.org/w/api.php"

###########
# Classes #
###########


@dataclass
class Page:
    """
    Wikipedia article in plain text.

    Attributes
    ----------
    title : str
        Canonical title (after redirects).
    url : str
        Permanent link to the revision.
    text : str
        Plain-text extract with "== Heading ==" lines.
    disambiguation : bool
        Whether the page is a disambiguation page.
    """

    title: str
    url: str
    text: str
    disambiguation: bool = False


class WikipediaClient:
    """
    Fetch articles, search titles and search around coordinates.

    Parameters
    ----------
    lang : str
        Wikipedia language ("fr", "en"...).
    cache : DiskCache or None
        Cache of the API answers.
    should_stop : callable, optional
        Cancellation check.
    """

    def __init__(self, lang: str = "fr", cache: Optional[DiskCache] = None,
                 should_stop: Optional[Callable[[], bool]] = None):
        self.lang = lang
        self.cache = cache or DiskCache(None)
        self.should_stop = should_stop

    def _query(self, params: dict) -> dict:
        """
        Run an API query, using the cache.

        Parameters
        ----------
        params : dict
            Query parameters.

        Returns
        -------
        dict
            Decoded answer.
        """
        params = dict(params, format="json", formatversion=2)
        url = build_url(API_URL % self.lang, dict(sorted(params.items())))
        cached = self.cache.get("wiki", url)
        if cached is not None:
            return cached
        answer = request_json(url, timeout=30, should_stop=self.should_stop)
        if isinstance(answer, dict) and "error" not in answer:
            self.cache.set("wiki", url, answer)
        return answer if isinstance(answer, dict) else {}

    def get_page(self, title: str) -> Optional[Page]:
        """
        Fetch an article, following redirects.

        Parameters
        ----------
        title : str
            Article title.

        Returns
        -------
        Page or None
            The article, None when it does not exist.
        """
        answer = self._query({
            "action": "query",
            "prop": "extracts|info|pageprops",
            "explaintext": 1,
            "exsectionformat": "wiki",
            "inprop": "url",
            "ppprop": "disambiguation",
            "redirects": 1,
            "titles": title,
        })
        pages = answer.get("query", {}).get("pages", [])
        if not pages:
            return None
        page = pages[0]
        if page.get("missing") or page.get("invalid") or not page.get("extract"):
            return None
        revision = page.get("lastrevid")
        if revision:
            url = "https://%s.wikipedia.org/w/index.php?%s" % (
                self.lang, urllib.parse.urlencode({"title": page["title"], "oldid": revision}))
        else:
            url = page.get("fullurl", "")
        return Page(
            title=page["title"],
            url=url,
            text=page["extract"],
            disambiguation="disambiguation" in page.get("pageprops", {}),
        )

    def search(self, query: str, limit: int = 5) -> List[str]:
        """
        Full-text search of article titles.

        Parameters
        ----------
        query : str
            Search terms.
        limit : int
            Maximum number of results.

        Returns
        -------
        list of str
            Titles, best first.
        """
        answer = self._query({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "srprop": "",
        })
        return [item["title"] for item in answer.get("query", {}).get("search", [])]

    def geosearch(self, latitude: float, longitude: float, radius: int = 10000,
                  limit: int = 10) -> List[str]:
        """
        Search articles located around coordinates.

        Parameters
        ----------
        latitude : float
            Latitude in decimal degrees.
        longitude : float
            Longitude in decimal degrees.
        radius : int
            Radius in meters (max 10000).
        limit : int
            Maximum number of results.

        Returns
        -------
        list of str
            Titles, nearest first.
        """
        answer = self._query({
            "action": "query",
            "list": "geosearch",
            "gscoord": "%.5f|%.5f" % (latitude, longitude),
            "gsradius": radius,
            "gslimit": limit,
        })
        return [item["title"] for item in answer.get("query", {}).get("geosearch", [])]
