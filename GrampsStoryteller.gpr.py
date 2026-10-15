#
# GrampsStoryteller - sourced historical context for the persons of a tree
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#

###########
# Imports #
###########

# Dependencies #
from gramps.version import major_version

#############
# Execution #
#############

register(
    TOOL,
    id="gramps_storyteller",
    name=_("Storyteller: historical context"),
    description=_("Writes, in a note, the sourced historical context of the places and "
                  "periods where a person lived, without sending any identifying data."),
    version="0.5.5",
    gramps_target_version=major_version,
    status=STABLE,
    fname="GrampsStoryteller.py",
    authors=["Paul Creusy"],
    authors_email=["paul.creusy@gmail.com"],
    help_url="https://github.com/PaulCreusy/GrampsStoryteller#readme",
    category=TOOL_DBPROC,
    toolclass="StorytellerTool",
    optionclass="StorytellerOptions",
    tool_modes=[TOOL_MODE_GUI],
)
