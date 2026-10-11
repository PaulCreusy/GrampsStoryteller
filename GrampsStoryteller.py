"""
Entry point loaded by Gramps (see GrampsStoryteller.gpr.py).

The addon code lives in the "storyteller" package next to this file. The
package name is specific to the addon to avoid clashes with other addons
that put their own directory on sys.path.
"""

###########
# Imports #
###########

# Python imports #
import os
import sys

_ADDON_DIR = os.path.dirname(os.path.realpath(__file__))
if _ADDON_DIR not in sys.path:
    sys.path.append(_ADDON_DIR)

# Local imports #
# Gramps looks up toolclass and optionclass in this module
from storyteller.gui.tool_window import StorytellerOptions, StorytellerTool  # noqa: E402,F401
