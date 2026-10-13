"""
Translation helper usable both inside Gramps and in plain Python (tests).
"""

###########
# Imports #
###########

# Python imports #
import os

#############
# Constants #
#############

_ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#############
# Functions #
#############


def _load_translator():
    """
    Return the gettext function of the addon, or identity outside Gramps.

    Returns
    -------
    callable
        Function translating a message.
    """
    try:
        from gramps.gen.const import GRAMPS_LOCALE as glocale
    except ImportError:
        return lambda message: message
    try:
        translator = glocale.get_addon_translator(
            os.path.join(_ADDON_ROOT, "GrampsStoryteller.py"))
        return translator.gettext
    except (ValueError, OSError):
        return glocale.translation.gettext


_ = _load_translator()
