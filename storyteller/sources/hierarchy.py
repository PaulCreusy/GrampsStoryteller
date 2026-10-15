"""
Collect sourced context for a request, from the most local level to the
broadest one: commune, department, historical province, region, country and
national periods.

Small communes rarely have a detailed history section, so the broader levels
are always collected as a fallback.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set, Tuple

# Local imports #
from .. import fr_admin
from ..anonymizer import period_bounds
from ..fr_admin import normalize
from ..models import CollectedSources, ContextRequest, PlaceInfo, SourceExtract
from .sections import history_sections, lead_text, parse_sections, select_paragraphs
from .wikipedia import Page, WikipediaClient

#############
# Constants #
#############

LEVEL_COMMUNE = "commune"
LEVEL_DEPARTMENT = "department"
LEVEL_PROVINCE = "province"
LEVEL_REGION = "region"
LEVEL_COUNTRY = "country"
LEVEL_PERIOD = "period"

LOCAL_LEVELS = (LEVEL_COMMUNE, LEVEL_DEPARTMENT)

DEFAULT_BUDGETS: Dict[str, int] = {
    LEVEL_COMMUNE: 3500,
    LEVEL_DEPARTMENT: 2500,
    LEVEL_PROVINCE: 2500,
    LEVEL_REGION: 1500,
    LEVEL_COUNTRY: 2500,
    LEVEL_PERIOD: 7000,
    "commune_lead": 800,
    "period_item": 1500,
}

# Characters of local (commune + department) excerpts above which the sources
# are considered rich enough to forbid general knowledge
DEFAULT_RICH_THRESHOLD = 1500

# The history excerpt of an administrative entity is completed by its
# dedicated history article (e.g. "Histoire du Nord-Pas-de-Calais") as long as
# at least this many characters of its budget remain
MIN_COMPLEMENT_CHARS = 500

# Year at which the current French regions were created
CURRENT_REGIONS_YEAR = 2016

# (first year, last year, French title, English title)
# Periods that changed the daily life of everyone (wars, revolution): they
# keep a full excerpt when the landmarks span many periods
MAJOR_PERIODS = {"Révolution française", "Guerre franco-allemande de 1870",
                 "Première Guerre mondiale", "Seconde Guerre mondiale"}
# Smallest excerpt of the other periods
MIN_PERIOD_ITEM = 350

# Articles about France during a period, tried before the general article
# (whose lead often describes the world, not life in France), then found by
# a search whose result title must contain every keyword ("a|b": one of
# them). Titles that do not exist are simply skipped.
PERIOD_ARTICLES: Dict[str, Dict[str, Tuple[List[str], str, List[str]]]] = {
    "Première Guerre mondiale": {
        "fr": (["La France dans la Première Guerre mondiale",
                "France pendant la Première Guerre mondiale",
                "Histoire de la France pendant la Première Guerre mondiale"],
               "France Première Guerre mondiale", ["france", "premiere guerre mondiale"]),
        "en": (["France in World War I", "France during World War I"],
               "France World War I", ["france", "world war i"]),
    },
    "Entre-deux-guerres": {
        "fr": (["France de l'entre-deux-guerres", "France dans l'entre-deux-guerres",
                "Histoire de la France dans l'entre-deux-guerres"],
               "France entre-deux-guerres", ["france", "entre deux guerres"]),
        "en": (["France in the interwar period", "Interwar France"],
               "France interwar period", ["france", "interwar"]),
    },
    "Seconde Guerre mondiale": {
        "fr": (["Occupation de la France par l'Allemagne pendant la Seconde Guerre mondiale",
                "France pendant la Seconde Guerre mondiale",
                "Histoire de la France pendant la Seconde Guerre mondiale"],
               "France Seconde Guerre mondiale occupation",
               ["france", "seconde guerre mondiale"]),
        "en": (["German occupation of France during World War II",
                "Military history of France during World War II"],
               "France World War II occupation", ["france", "world war ii"]),
    },
}

# Departments wholly or partly occupied by the German army in 1914-1918
OCCUPIED_1914 = {"02", "08", "51", "54", "55", "59", "60", "62", "80", "88"}
OCCUPATION_1914_ARTICLES: Dict[str, Tuple[List[str], str, List[str]]] = {
    "fr": (["Occupation du Nord de la France pendant la Première Guerre mondiale",
            "Occupation allemande du Nord de la France pendant la Première Guerre mondiale",
            "Nord de la France pendant la Première Guerre mondiale"],
           "occupation Nord de la France Première Guerre mondiale",
           ["occupation", "nord", "premiere guerre mondiale|1914"]),
    "en": (["German occupation of northern France during World War I",
            "Occupation of northern France during World War I"],
           "German occupation northern France World War I",
           ["occupation", "france", "world war i|1914"]),
}

FRANCE_PERIODS: List[Tuple[int, int, str, str]] = [
    (987, 1589, "Royaume de France", "Kingdom of France"),
    (1589, 1789, "Ancien Régime", "Ancien Régime"),
    (1789, 1799, "Révolution française", "French Revolution"),
    (1799, 1804, "Consulat (histoire de France)", "French Consulate"),
    (1804, 1815, "Premier Empire", "First French Empire"),
    (1814, 1830, "Restauration (histoire de France)", "Bourbon Restoration in France"),
    (1830, 1848, "Monarchie de Juillet", "July Monarchy"),
    (1848, 1852, "Deuxième République (France)", "French Second Republic"),
    (1852, 1870, "Second Empire", "Second French Empire"),
    (1870, 1871, "Guerre franco-allemande de 1870", "Franco-Prussian War"),
    (1870, 1940, "Troisième République (France)", "French Third Republic"),
    (1871, 1914, "Belle Époque", "Belle Époque"),
    (1914, 1918, "Première Guerre mondiale", "World War I"),
    (1919, 1939, "Entre-deux-guerres", "Interwar period"),
    (1939, 1945, "Seconde Guerre mondiale", "World War II"),
    (1940, 1944, "Régime de Vichy", "Vichy France"),
    (1944, 1946, "Gouvernement provisoire de la République française",
     "Provisional Government of the French Republic"),
    (1946, 1958, "Quatrième République (France)", "French Fourth Republic"),
    (1958, 2100, "Cinquième République (France)", "French Fifth Republic"),
]

_ADMIN_CANDIDATES = {
    "fr": {
        LEVEL_DEPARTMENT: ["{} (département)", "{} (département français)", "{}"],
        LEVEL_PROVINCE: ["{} (province)", "{}"],
        LEVEL_REGION: ["{} (région administrative)", "{} (région)", "{}"],
    },
    "en": {
        LEVEL_DEPARTMENT: ["{} (department)", "{} (French department)", "{}"],
        LEVEL_PROVINCE: ["{} (province)", "{}"],
        LEVEL_REGION: ["{} (region)", "{} (administrative region)", "{}"],
    },
}

_ADMIN_KEYWORDS = {
    "fr": {
        LEVEL_DEPARTMENT: "departement",
        LEVEL_PROVINCE: "france",
        LEVEL_REGION: "region",
    },
    "en": {
        LEVEL_DEPARTMENT: "department",
        LEVEL_PROVINCE: "france",
        LEVEL_REGION: "region",
    },
}

_HISTORY_ARTICLES = {
    "fr": ["Histoire de {}", "Histoire de la {}", "Histoire de l'{}", "Histoire du {}",
           "Histoire des {}"],
    "en": ["History of {}", "History of the {}"],
}

_MESSAGES = {
    "fr": {
        "code_conflict": "Le code « %s » du lieu « %s » ne correspond pas au département « %s » "
                         "de la hiérarchie : la hiérarchie est utilisée.",
        "commune_missing": "Aucun article Wikipédia trouvé pour la commune « %s ».",
        "no_history": "L'article « %s » n'a pas de section historique exploitable.",
    },
    "en": {
        "code_conflict": "The code \"%s\" of the place \"%s\" does not match the department "
                         "\"%s\" of the hierarchy: the hierarchy is used.",
        "commune_missing": "No Wikipedia article found for the place \"%s\".",
        "no_history": "The article \"%s\" has no usable history section.",
    },
}

#############
# Functions #
#############


def _without_repeats(text: str, previous: str) -> str:
    """
    Remove the paragraphs of a text already present in a previous excerpt.

    Parameters
    ----------
    text : str
        New excerpt, one paragraph per line.
    previous : str
        Excerpt already given.

    Returns
    -------
    str
        New excerpt without the repeated paragraphs (headings kept only when
        followed by a paragraph).
    """
    known = {normalize(line)[:120] for line in previous.splitlines() if line.strip()}
    lines = [line for line in text.splitlines()
             if line.startswith("— ") or normalize(line)[:120] not in known]
    kept = []
    for index, line in enumerate(lines):
        is_heading = line.startswith("— ")
        next_is_text = index + 1 < len(lines) and not lines[index + 1].startswith("— ")
        if not is_heading or next_is_text:
            kept.append(line)
    return "\n".join(kept)


###########
# Classes #
###########


class SourceCollector:
    """
    Gather numbered Wikipedia excerpts for a request.

    Parameters
    ----------
    client : WikipediaClient
        Wikipedia client, in the language of the text.
    budgets : dict, optional
        Maximum characters per level.
    rich_threshold : int
        Local characters above which the sources are considered rich.
    progress : callable, optional
        Called with a short status message.
    """

    def __init__(self, client: WikipediaClient, budgets: Optional[Dict[str, int]] = None,
                 rich_threshold: int = DEFAULT_RICH_THRESHOLD,
                 progress: Optional[Callable[[str], None]] = None):
        self.client = client
        self.lang = client.lang if client.lang in _ADMIN_CANDIDATES else "en"
        self.budgets = dict(DEFAULT_BUDGETS, **(budgets or {}))
        self.rich_threshold = rich_threshold
        self.progress = progress or (lambda message: None)
        self.messages = _MESSAGES[self.lang]

    # Public API

    def collect(self, request: ContextRequest) -> CollectedSources:
        """
        Collect the excerpts of every level for a request.

        Parameters
        ----------
        request : ContextRequest
            Anonymized request.

        Returns
        -------
        CollectedSources
            Numbered excerpts, richness and warnings.
        """
        low, high = period_bounds(request)
        result = CollectedSources()
        seen: Set[str] = set()
        has_france = False
        france_departments: Set[str] = set()

        for place in request.places:
            dep_code, in_france = self._resolve_department(place, result.warnings)
            has_france = has_france or in_france
            if in_france and dep_code:
                france_departments.add(dep_code)
            department = place.department or fr_admin.department_name(dep_code)

            self.progress(place.name)
            commune = self._find_commune(place, department)
            if commune is None:
                result.warnings.append(self.messages["commune_missing"] % place.name)
            else:
                self._add_commune(commune, low, high, seen, result)

            if department:
                page = self._find_admin(department, LEVEL_DEPARTMENT, place.country)
                self._add_history(page, department, LEVEL_DEPARTMENT, low, high, seen, result)

            if in_france and dep_code:
                province = fr_admin.province_name(dep_code, self.lang)
                page = self._find_admin(province, LEVEL_PROVINCE, None)
                self._add_history(page, province, LEVEL_PROVINCE, low, high, seen, result)
                if high is not None and high >= CURRENT_REGIONS_YEAR:
                    region = fr_admin.region_name(dep_code, self.lang)
                    if region and region != province:
                        page = self._find_admin(region, LEVEL_REGION, None)
                        self._add_history(page, region, LEVEL_REGION, low, high, seen, result)
            elif place.region:
                page = self._find_admin(place.region, LEVEL_REGION, place.country)
                self._add_history(page, place.region, LEVEL_REGION, low, high, seen, result)

            if place.country and not in_france:
                page = self._find_history_article(place.country)
                if page is not None and page.title not in seen:
                    sections = parse_sections(page.text)[1:]
                    text = select_paragraphs(sections, low, high, self.budgets[LEVEL_COUNTRY])
                    self._append(page, LEVEL_COUNTRY, text, seen, result)

        if has_france and request.year_min is not None:
            self._add_france_periods(request.year_min, request.year_max, seen, result,
                                     france_departments)

        for number, extract in enumerate(result.extracts, start=1):
            extract.number = number
        local_chars = sum(len(e.text) for e in result.extracts if e.level in LOCAL_LEVELS)
        result.rich = local_chars >= self.rich_threshold
        return result

    # Resolution

    def _resolve_department(self, place: PlaceInfo, warnings: List[str]) -> Tuple[Optional[str], bool]:
        """
        Find the department code of a place and whether it is in France.

        The postal/INSEE code wins, unless the Gramps hierarchy names another
        department, in which case the hierarchy wins and a warning is issued.

        Parameters
        ----------
        place : PlaceInfo
            Place.
        warnings : list of str
            Warning list to complete.

        Returns
        -------
        tuple of (str or None, bool)
            Department code and France flag.
        """
        if place.country and not fr_admin.is_france(place.country):
            return None, False
        from_code = fr_admin.department_code_from_place_code(place.code, place.department)
        from_name = fr_admin.department_code_from_name(place.department)
        if from_code and from_name and from_code != from_name:
            warnings.append(self.messages["code_conflict"] % (place.code, place.name, place.department))
            return from_name, True
        code = from_code or from_name
        return code, bool(code) or fr_admin.is_france(place.country)

    def _valid(self, page: Optional[Page], keyword: Optional[str]) -> bool:
        """
        Check that a page is a real article mentioning a keyword.

        Parameters
        ----------
        page : Page or None
            Candidate page.
        keyword : str or None
            Normalized word expected in the beginning of the article.

        Returns
        -------
        bool
            True when the page is acceptable.
        """
        if page is None or page.disambiguation:
            return False
        if not keyword:
            return True
        return normalize(keyword) in normalize(page.text[:6000])

    def _find_commune(self, place: PlaceInfo, department: Optional[str]) -> Optional[Page]:
        """
        Find the article of a commune: exact titles, then search, then
        coordinates.

        Parameters
        ----------
        place : PlaceInfo
            Place.
        department : str or None
            Department name used to disambiguate.

        Returns
        -------
        Page or None
            Article, None when not found.
        """
        check = department or place.country
        candidates = []
        if department:
            candidates.append("%s (%s)" % (place.name, department) if self.lang == "fr"
                              else "%s, %s" % (place.name, department))
        candidates.append(place.name)
        for title in candidates:
            page = self.client.get_page(title)
            if self._valid(page, check):
                return page

        wanted = normalize(place.name)
        query = " ".join(part for part in (place.name, department or place.country or "") if part)
        titles = [t for t in self.client.search(query) if normalize(t).startswith(wanted)]
        if place.latitude is not None and place.longitude is not None:
            titles += [t for t in self.client.geosearch(place.latitude, place.longitude)
                       if normalize(t).startswith(wanted) and t not in titles]
        for title in titles[:5]:
            page = self.client.get_page(title)
            if self._valid(page, check):
                return page
        return None

    def _find_admin(self, name: Optional[str], level: str, country: Optional[str]) -> Optional[Page]:
        """
        Find the article of a department, province or region.

        Parameters
        ----------
        name : str or None
            Entity name.
        level : str
            LEVEL_DEPARTMENT, LEVEL_PROVINCE or LEVEL_REGION.
        country : str or None
            Country, used to validate foreign entities.

        Returns
        -------
        Page or None
            Article, None when not found.
        """
        if not name:
            return None
        foreign = bool(country) and not fr_admin.is_france(country)
        keyword = country if foreign else _ADMIN_KEYWORDS[self.lang][level]
        patterns = ["{}"] if foreign else _ADMIN_CANDIDATES[self.lang][level]
        for pattern in patterns:
            page = self.client.get_page(pattern.format(name))
            if self._valid(page, keyword):
                return page
        for title in self.client.search(" ".join(p for p in (name, country or "") if p))[:3]:
            if normalize(title).startswith(normalize(name)):
                page = self.client.get_page(title)
                if self._valid(page, keyword):
                    return page
        return None

    def _find_history_article(self, name: str) -> Optional[Page]:
        """
        Find the dedicated history article of a country, province or
        department (e.g. "Histoire de la Haute-Saône").

        Parameters
        ----------
        name : str
            Entity name.

        Returns
        -------
        Page or None
            Article, None when not found.
        """
        wanted = normalize(name)
        for pattern in _HISTORY_ARTICLES[self.lang]:
            page = self.client.get_page(pattern.format(name))
            if self._valid(page, None) and wanted in normalize(page.title):
                return page
        prefix = "histoire" if self.lang == "fr" else "history"
        for title in self.client.search("%s %s" % (prefix, name))[:5]:
            normalized = normalize(title)
            if normalized.startswith(prefix) and wanted in normalized:
                page = self.client.get_page(title)
                if self._valid(page, None):
                    return page
        return None

    # Excerpts

    def _append(self, page: Page, level: str, text: str, seen: Set[str],
                result: CollectedSources, span: str = ""):
        """
        Register an excerpt if it is new and not empty.

        Parameters
        ----------
        page : Page
            Source article.
        level : str
            Level of the excerpt.
        text : str
            Excerpt.
        seen : set of str
            Titles already used.
        result : CollectedSources
            Result to complete.
        span : str
            Years of a national period.
        """
        if not text.strip() or page.title in seen:
            return
        seen.add(page.title)
        result.extracts.append(SourceExtract(title=page.title, level=level, url=page.url, text=text,
                                             span=span))

    def _add_commune(self, page: Page, low: Optional[int], high: Optional[int], seen: Set[str],
                     result: CollectedSources):
        """
        Add the excerpt of a commune: its lead (geography) and its history.

        Parameters
        ----------
        page : Page
            Commune article.
        low : int or None
            Lower bound of the period.
        high : int or None
            Upper bound of the period.
        seen : set of str
            Titles already used.
        result : CollectedSources
            Result to complete.
        """
        sections = parse_sections(page.text)
        lead = lead_text(sections, self.budgets["commune_lead"])
        history = select_paragraphs(history_sections(sections), low, high,
                                    self.budgets[LEVEL_COMMUNE])
        if not history:
            result.warnings.append(self.messages["no_history"] % page.title)
        self._append(page, LEVEL_COMMUNE, "\n".join(p for p in (lead, history) if p), seen, result)

    def _add_history(self, page: Optional[Page], name: str, level: str, low: Optional[int],
                     high: Optional[int], seen: Set[str], result: CollectedSources):
        """
        Add the history excerpt of an administrative entity, completed by its
        dedicated history article while the budget allows it.

        Parameters
        ----------
        page : Page or None
            Article, None when not found.
        name : str
            Entity name, used to find its history article.
        level : str
            Level of the excerpt.
        low : int or None
            Lower bound of the period.
        high : int or None
            Upper bound of the period.
        seen : set of str
            Titles already used.
        result : CollectedSources
            Result to complete.
        """
        text = ""
        if page is not None and page.title not in seen:
            sections = history_sections(parse_sections(page.text))
            text = select_paragraphs(sections, low, high, self.budgets[level])
            self._append(page, level, text, seen, result)
        budget = self.budgets[level] - len(text)
        if budget < MIN_COMPLEMENT_CHARS or not name:
            return
        article = self._find_history_article(name)
        if article is None or article.title in seen:
            return
        complement = select_paragraphs(parse_sections(article.text), low, high, budget)
        self._append(article, level, _without_repeats(complement, text), seen, result)

    def _add_france_periods(self, first_year: int, last_year: Optional[int], seen: Set[str],
                            result: CollectedSources,
                            departments: Optional[Set[str]] = None):
        """
        Add the national periods overlapping the years, preferring articles
        about France during a war to the general article.

        Parameters
        ----------
        first_year : int
            Earliest landmark year.
        last_year : int or None
            Latest landmark year.
        seen : set of str
            Titles already used.
        result : CollectedSources
            Result to complete.
        departments : set of str, optional
            Codes of the French departments of the landmarks.
        """
        last_year = last_year if last_year is not None else first_year
        periods = [p for p in FRANCE_PERIODS if p[0] <= last_year and p[1] >= first_year]
        if not periods:
            return
        occupied = bool((departments or set()) & OCCUPIED_1914) and \
            any(p[2] == "Première Guerre mondiale" for p in periods)
        item = self.budgets["period_item"]
        majors = sum(1 for p in periods if p[2] in MAJOR_PERIODS) + int(occupied)
        minors = len(periods) + int(occupied) - majors
        remaining = self.budgets[LEVEL_PERIOD] - majors * item
        minor_budget = min(item, max(MIN_PERIOD_ITEM, remaining // minors)) if minors else 0
        for first, last, title_fr, title_en in periods:
            title = title_fr if self.lang == "fr" else title_en
            self.progress(title)
            page = self._period_page(title, PERIOD_ARTICLES.get(title_fr, {}).get(self.lang))
            if page is None:
                continue
            budget = item if title_fr in MAJOR_PERIODS else minor_budget
            span = "%d–%d" % (first, last) if last < 2100 else "%d–" % first
            self._append(page, LEVEL_PERIOD, lead_text(parse_sections(page.text), budget),
                         seen, result, span)
            if occupied and title_fr == "Première Guerre mondiale":
                page = self._period_page(None, OCCUPATION_1914_ARTICLES.get(self.lang))
                if page is not None:
                    self._append(page, LEVEL_PERIOD, lead_text(parse_sections(page.text), item),
                                 seen, result, span)

    def _period_page(self, general: Optional[str],
                     spec: Optional[Tuple[List[str], str, List[str]]]) -> Optional[Page]:
        """
        Find the article of a period: a specific article, a matching search
        result, then the general article.

        Parameters
        ----------
        general : str or None
            Title of the general article, None when there is no fallback.
        spec : tuple or None
            (specific titles, search query, keywords that a search result
            title must all contain).

        Returns
        -------
        Page or None
            Article.
        """
        if spec is not None:
            titles, query, keywords = spec
            for title in titles:
                page = self.client.get_page(title)
                if self._valid(page, None):
                    return page
            for title in self.client.search(query):
                # Whole words: "world war i" must not match "world war ii"
                normalized = " %s " % normalize(title)
                if all(any(" %s " % normalize(word) in normalized for word in keyword.split("|"))
                       for keyword in keywords):
                    page = self.client.get_page(title)
                    if self._valid(page, None):
                        return page
        if general is None:
            return None
        page = self.client.get_page(general)
        return page if self._valid(page, None) else None
