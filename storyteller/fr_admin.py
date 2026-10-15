"""
French administrative tables, used locally to resolve the department,
the current region and the historical province of a place from its postal
or INSEE code. No network access is involved.

The historical province is an approximation: departments created in 1790
do not exactly follow the borders of the former provinces.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional, Tuple

#############
# Constants #
#############

# Department code -> (name, current region key, historical province key)
DEPARTMENTS: Dict[str, Tuple[str, str, str]] = {
    "01": ("Ain", "ARA", "Bourgogne"),
    "02": ("Aisne", "HDF", "Picardie"),
    "03": ("Allier", "ARA", "Bourbonnais"),
    "04": ("Alpes-de-Haute-Provence", "PAC", "Provence"),
    "05": ("Hautes-Alpes", "PAC", "Dauphiné"),
    "06": ("Alpes-Maritimes", "PAC", "Comté de Nice"),
    "07": ("Ardèche", "ARA", "Vivarais"),
    "08": ("Ardennes", "GES", "Champagne"),
    "09": ("Ariège", "OCC", "Comté de Foix"),
    "10": ("Aube", "GES", "Champagne"),
    "11": ("Aude", "OCC", "Languedoc"),
    "12": ("Aveyron", "OCC", "Rouergue"),
    "13": ("Bouches-du-Rhône", "PAC", "Provence"),
    "14": ("Calvados", "NOR", "Normandie"),
    "15": ("Cantal", "ARA", "Auvergne"),
    "16": ("Charente", "NAQ", "Angoumois"),
    "17": ("Charente-Maritime", "NAQ", "Saintonge"),
    "18": ("Cher", "CVL", "Berry"),
    "19": ("Corrèze", "NAQ", "Limousin"),
    "2A": ("Corse-du-Sud", "COR", "Corse"),
    "2B": ("Haute-Corse", "COR", "Corse"),
    "21": ("Côte-d'Or", "BFC", "Bourgogne"),
    "22": ("Côtes-d'Armor", "BRE", "Bretagne"),
    "23": ("Creuse", "NAQ", "Marche"),
    "24": ("Dordogne", "NAQ", "Périgord"),
    "25": ("Doubs", "BFC", "Franche-Comté"),
    "26": ("Drôme", "ARA", "Dauphiné"),
    "27": ("Eure", "NOR", "Normandie"),
    "28": ("Eure-et-Loir", "CVL", "Orléanais"),
    "29": ("Finistère", "BRE", "Bretagne"),
    "30": ("Gard", "OCC", "Languedoc"),
    "31": ("Haute-Garonne", "OCC", "Languedoc"),
    "32": ("Gers", "OCC", "Gascogne"),
    "33": ("Gironde", "NAQ", "Guyenne"),
    "34": ("Hérault", "OCC", "Languedoc"),
    "35": ("Ille-et-Vilaine", "BRE", "Bretagne"),
    "36": ("Indre", "CVL", "Berry"),
    "37": ("Indre-et-Loire", "CVL", "Touraine"),
    "38": ("Isère", "ARA", "Dauphiné"),
    "39": ("Jura", "BFC", "Franche-Comté"),
    "40": ("Landes", "NAQ", "Gascogne"),
    "41": ("Loir-et-Cher", "CVL", "Orléanais"),
    "42": ("Loire", "ARA", "Forez"),
    "43": ("Haute-Loire", "ARA", "Velay"),
    "44": ("Loire-Atlantique", "PDL", "Bretagne"),
    "45": ("Loiret", "CVL", "Orléanais"),
    "46": ("Lot", "OCC", "Quercy"),
    "47": ("Lot-et-Garonne", "NAQ", "Agenais"),
    "48": ("Lozère", "OCC", "Gévaudan"),
    "49": ("Maine-et-Loire", "PDL", "Anjou"),
    "50": ("Manche", "NOR", "Normandie"),
    "51": ("Marne", "GES", "Champagne"),
    "52": ("Haute-Marne", "GES", "Champagne"),
    "53": ("Mayenne", "PDL", "Maine"),
    "54": ("Meurthe-et-Moselle", "GES", "Lorraine"),
    "55": ("Meuse", "GES", "Lorraine"),
    "56": ("Morbihan", "BRE", "Bretagne"),
    "57": ("Moselle", "GES", "Lorraine"),
    "58": ("Nièvre", "BFC", "Nivernais"),
    "59": ("Nord", "HDF", "Flandre française"),
    "60": ("Oise", "HDF", "Picardie"),
    "61": ("Orne", "NOR", "Normandie"),
    "62": ("Pas-de-Calais", "HDF", "Artois"),
    "63": ("Puy-de-Dôme", "ARA", "Auvergne"),
    "64": ("Pyrénées-Atlantiques", "NAQ", "Béarn"),
    "65": ("Hautes-Pyrénées", "OCC", "Bigorre"),
    "66": ("Pyrénées-Orientales", "OCC", "Roussillon"),
    "67": ("Bas-Rhin", "GES", "Alsace"),
    "68": ("Haut-Rhin", "GES", "Alsace"),
    "69": ("Rhône", "ARA", "Lyonnais"),
    "70": ("Haute-Saône", "BFC", "Franche-Comté"),
    "71": ("Saône-et-Loire", "BFC", "Bourgogne"),
    "72": ("Sarthe", "PDL", "Maine"),
    "73": ("Savoie", "ARA", "Savoie"),
    "74": ("Haute-Savoie", "ARA", "Savoie"),
    "75": ("Paris", "IDF", "Île-de-France"),
    "76": ("Seine-Maritime", "NOR", "Normandie"),
    "77": ("Seine-et-Marne", "IDF", "Île-de-France"),
    "78": ("Yvelines", "IDF", "Île-de-France"),
    "79": ("Deux-Sèvres", "NAQ", "Poitou"),
    "80": ("Somme", "HDF", "Picardie"),
    "81": ("Tarn", "OCC", "Languedoc"),
    "82": ("Tarn-et-Garonne", "OCC", "Quercy"),
    "83": ("Var", "PAC", "Provence"),
    "84": ("Vaucluse", "PAC", "Comtat Venaissin"),
    "85": ("Vendée", "PDL", "Poitou"),
    "86": ("Vienne", "NAQ", "Poitou"),
    "87": ("Haute-Vienne", "NAQ", "Limousin"),
    "88": ("Vosges", "GES", "Lorraine"),
    "89": ("Yonne", "BFC", "Bourgogne"),
    "90": ("Territoire de Belfort", "BFC", "Alsace"),
    "91": ("Essonne", "IDF", "Île-de-France"),
    "92": ("Hauts-de-Seine", "IDF", "Île-de-France"),
    "93": ("Seine-Saint-Denis", "IDF", "Île-de-France"),
    "94": ("Val-de-Marne", "IDF", "Île-de-France"),
    "95": ("Val-d'Oise", "IDF", "Île-de-France"),
    "971": ("Guadeloupe", "GUA", "Guadeloupe"),
    "972": ("Martinique", "MAR", "Martinique"),
    "973": ("Guyane", "GUF", "Guyane"),
    "974": ("La Réunion", "REU", "La Réunion"),
    "976": ("Mayotte", "MAY", "Mayotte"),
}

# Former department names still found in family trees
DEPARTMENT_ALIASES: Dict[str, str] = {
    "cotes du nord": "22",
    "loire inferieure": "44",
    "seine inferieure": "76",
    "basses alpes": "04",
    "basses pyrenees": "64",
    "charente inferieure": "17",
    "meurthe": "54",
    "corse": "2A",
}

# Current region key -> (French name, English name)
REGIONS: Dict[str, Tuple[str, str]] = {
    "ARA": ("Auvergne-Rhône-Alpes", "Auvergne-Rhône-Alpes"),
    "BFC": ("Bourgogne-Franche-Comté", "Bourgogne-Franche-Comté"),
    "BRE": ("Bretagne", "Brittany"),
    "CVL": ("Centre-Val de Loire", "Centre-Val de Loire"),
    "COR": ("Corse", "Corsica"),
    "GES": ("Grand Est", "Grand Est"),
    "HDF": ("Hauts-de-France", "Hauts-de-France"),
    "IDF": ("Île-de-France", "Île-de-France"),
    "NOR": ("Normandie", "Normandy"),
    "NAQ": ("Nouvelle-Aquitaine", "Nouvelle-Aquitaine"),
    "OCC": ("Occitanie", "Occitania"),
    "PDL": ("Pays de la Loire", "Pays de la Loire"),
    "PAC": ("Provence-Alpes-Côte d'Azur", "Provence-Alpes-Côte d'Azur"),
    "GUA": ("Guadeloupe", "Guadeloupe"),
    "MAR": ("Martinique", "Martinique"),
    "GUF": ("Guyane", "French Guiana"),
    "REU": ("La Réunion", "Réunion"),
    "MAY": ("Mayotte", "Mayotte"),
}

# Historical province (French name) -> English Wikipedia name
PROVINCES_EN: Dict[str, str] = {
    "Agenais": "Agenais",
    "Alsace": "Alsace",
    "Angoumois": "Angoumois",
    "Anjou": "Anjou",
    "Artois": "Artois",
    "Auvergne": "Auvergne",
    "Béarn": "Béarn",
    "Berry": "Berry",
    "Bigorre": "Bigorre",
    "Bourbonnais": "Bourbonnais",
    "Bourgogne": "Burgundy",
    "Bretagne": "Brittany",
    "Champagne": "Champagne",
    "Comtat Venaissin": "Comtat Venaissin",
    "Comté de Foix": "County of Foix",
    "Comté de Nice": "County of Nice",
    "Corse": "Corsica",
    "Dauphiné": "Dauphiné",
    "Flandre française": "French Flanders",
    "Forez": "Forez",
    "Franche-Comté": "Franche-Comté",
    "Gascogne": "Gascony",
    "Gévaudan": "Gévaudan",
    "Guadeloupe": "Guadeloupe",
    "Guyane": "French Guiana",
    "Guyenne": "Guyenne",
    "Île-de-France": "Île-de-France",
    "La Réunion": "Réunion",
    "Languedoc": "Languedoc",
    "Limousin": "Limousin",
    "Lorraine": "Lorraine",
    "Lyonnais": "Lyonnais",
    "Maine": "Maine",
    "Marche": "Marche",
    "Martinique": "Martinique",
    "Mayotte": "Mayotte",
    "Nivernais": "Nivernais",
    "Normandie": "Normandy",
    "Orléanais": "Orléanais",
    "Périgord": "Périgord",
    "Picardie": "Picardy",
    "Poitou": "Poitou",
    "Provence": "Provence",
    "Quercy": "Quercy",
    "Rouergue": "Rouergue",
    "Roussillon": "Roussillon",
    "Saintonge": "Saintonge",
    "Savoie": "Savoy",
    "Touraine": "Touraine",
    "Velay": "Velay",
    "Vivarais": "Vivarais",
}

FRANCE_NAMES = ("france", "republique francaise", "french republic", "royaume de france")

_CODE_PATTERN = re.compile(r"^(?:2[AB]\d{3}|97\d{3}|\d{5})$")

#############
# Functions #
#############


def normalize(text: str) -> str:
    """
    Normalize a name for comparisons: no accents, lower case, no punctuation.

    Parameters
    ----------
    text : str
        Name.

    Returns
    -------
    str
        Normalized name.
    """
    # Ligatures have no decomposition (Annœullin = Annoeullin)
    text = (text or "").replace("œ", "oe").replace("Œ", "Oe").replace("æ", "ae").replace("Æ", "Ae")
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = re.sub(r"[^a-zA-Z0-9]+", " ", stripped)
    return stripped.strip().lower()


def is_france(country: Optional[str]) -> bool:
    """
    Tell whether a country name designates France.

    Parameters
    ----------
    country : str or None
        Country name.

    Returns
    -------
    bool
        True for France.
    """
    return bool(country) and normalize(country) in FRANCE_NAMES


def department_code_from_name(name: Optional[str]) -> Optional[str]:
    """
    Find a department code from its (current or former) name.

    Parameters
    ----------
    name : str or None
        Department name, e.g. "Finistère" or "Côtes-du-Nord".

    Returns
    -------
    str or None
        Department code, None when unknown.
    """
    if not name:
        return None
    wanted = normalize(name)
    for code, (dep_name, _region, _province) in DEPARTMENTS.items():
        if normalize(dep_name) == wanted:
            return code
    return DEPARTMENT_ALIASES.get(wanted)


def department_code_from_place_code(code: Optional[str],
                                    department_hint: Optional[str] = None) -> Optional[str]:
    """
    Derive the department code from a postal or INSEE code.

    Both codes have five characters and start with the department number, so
    only the department is derived. Corsica postal codes ("20xxx") are split
    using the hint or the postal ranges.

    Parameters
    ----------
    code : str or None
        Postal or INSEE code.
    department_hint : str or None
        Department name from the Gramps hierarchy, used for Corsica.

    Returns
    -------
    str or None
        Department code, None when the code is not a French one.
    """
    if not code:
        return None
    code = code.replace(" ", "").upper()
    if not _CODE_PATTERN.match(code):
        return None
    if code.startswith("97"):
        prefix = code[:3]
        return prefix if prefix in DEPARTMENTS else None
    prefix = code[:2]
    if prefix == "20":
        hinted = department_code_from_name(department_hint)
        if hinted in ("2A", "2B"):
            return hinted
        # Postal codes 200xx-201xx belong to Corse-du-Sud
        return "2A" if int(code[2:4]) < 20 else "2B"
    return prefix if prefix in DEPARTMENTS else None


def department_name(code: Optional[str]) -> Optional[str]:
    """
    Return the department name of a department code.

    Parameters
    ----------
    code : str or None
        Department code.

    Returns
    -------
    str or None
        Name, None when unknown.
    """
    entry = DEPARTMENTS.get(code or "")
    return entry[0] if entry else None


def region_name(code: Optional[str], lang: str = "fr") -> Optional[str]:
    """
    Return the current region of a department.

    Parameters
    ----------
    code : str or None
        Department code.
    lang : str
        "fr" or "en".

    Returns
    -------
    str or None
        Region name, None when unknown.
    """
    entry = DEPARTMENTS.get(code or "")
    if not entry:
        return None
    names = REGIONS[entry[1]]
    return names[1] if lang == "en" else names[0]


def province_name(code: Optional[str], lang: str = "fr") -> Optional[str]:
    """
    Return the historical province of a department.

    Parameters
    ----------
    code : str or None
        Department code.
    lang : str
        "fr" or "en".

    Returns
    -------
    str or None
        Province name, None when unknown.
    """
    entry = DEPARTMENTS.get(code or "")
    if not entry:
        return None
    return PROVINCES_EN.get(entry[2], entry[2]) if lang == "en" else entry[2]
