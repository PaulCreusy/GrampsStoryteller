"""
Popup showing what will be sent: the anonymized landmarks of each person
and, on demand, the full request with the Wikipedia excerpts.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import threading
from typing import Callable, List, Optional

# Dependencies #
from gi.repository import GLib, Gtk

# Local imports #
from ..batch import BatchPlan
from ..i18n import _
from ..pipeline import StoryPipeline
from .widgets import set_text, text_view, wrapped_label

#############
# Constants #
#############

# Persons listed in the landmarks tab
MAX_LISTED_PERSONS = 500

###########
# Classes #
###########


class SentDataDialog:
    """
    Non-modal dialog with the landmarks and the full request.

    Parameters
    ----------
    parent : Gtk.Window
        Parent window.
    plan : BatchPlan
        Persons and their requests.
    settings : Settings
        Settings of the run (language, prompts, options).
    skip_reasons : dict
        Skip reason -> explanation.
    request_lines : callable
        Returns the displayed lines of a request.
    """

    def __init__(self, parent: Gtk.Window, plan: BatchPlan, settings, skip_reasons: dict,
                 request_lines: Callable[[object], List[str]]):
        self.plan = plan
        self.settings = settings
        self.stop_event = threading.Event()
        self.closed = False

        self.dialog = Gtk.Dialog(title=_("Data sent"), transient_for=parent, modal=False)
        self.dialog.set_default_size(760, 560)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", lambda *_a: self.close())
        self.dialog.connect("delete-event", lambda *_a: self.close() or True)

        notebook = Gtk.Notebook()
        notebook.append_page(self._build_landmarks(skip_reasons, request_lines),
                             Gtk.Label(label=_("Landmarks")))
        notebook.append_page(self._build_request(), Gtk.Label(label=_("Full request")))
        area = self.dialog.get_content_area()
        area.set_border_width(8)
        area.pack_start(notebook, True, True, 0)
        self.dialog.show_all()
        self.spinner.hide()

    # Pages

    def _build_landmarks(self, skip_reasons: dict,
                         request_lines: Callable[[object], List[str]]) -> Gtk.Widget:
        """
        Build the landmarks page.

        Parameters
        ----------
        skip_reasons : dict
            Skip reason -> explanation.
        request_lines : callable
            Returns the displayed lines of a request.

        Returns
        -------
        Gtk.Widget
            Page.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        box.pack_start(wrapped_label(_(
            "Only the indented lines are sent, to Wikipedia and to the LLM. The names are "
            "shown here for you only: no name, no identifier and no event type ever leave "
            "Gramps.")), False, False, 0)
        scrolled, view = text_view(monospace=True, height=380)
        lines = []
        for item in self.plan.items[:MAX_LISTED_PERSONS]:
            lines.append("%s [%s]" % (item.facts.name, item.facts.gramps_id))
            if item.skip_reason:
                lines.append("    — " + _("not processed: %s")
                             % skip_reasons.get(item.skip_reason, item.skip_reason))
            else:
                lines.extend("    %s" % line for line in request_lines(item.request))
        if len(self.plan.items) > MAX_LISTED_PERSONS:
            lines.append(_("... and %d more persons.")
                         % (len(self.plan.items) - MAX_LISTED_PERSONS))
        set_text(view, "\n".join(lines))
        box.pack_start(scrolled, True, True, 0)
        return box

    def _build_request(self) -> Gtk.Widget:
        """
        Build the full request page, filled on demand.

        Returns
        -------
        Gtk.Widget
            Page.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        groups = len(self.plan.groups)
        if groups > 1:
            explanation = _("Shows the request of the first context (out of %d): the Wikipedia "
                            "excerpts and the prompts. The excerpts are fetched from Wikipedia; "
                            "nothing is sent to the LLM.") % groups
        else:
            explanation = _("Shows the request: the Wikipedia excerpts and the prompts. The "
                            "excerpts are fetched from Wikipedia; nothing is sent to the LLM.")
        box.pack_start(wrapped_label(explanation), False, False, 0)

        row = Gtk.Box(spacing=8)
        self.fetch_button = Gtk.Button(label=_("Fetch the Wikipedia excerpts"))
        self.fetch_button.connect("clicked", self._on_fetch)
        self.fetch_button.set_sensitive(groups > 0)
        self.spinner = Gtk.Spinner()
        self.request_status = wrapped_label()
        row.pack_start(self.fetch_button, False, False, 0)
        row.pack_start(self.spinner, False, False, 0)
        row.pack_start(self.request_status, True, True, 0)
        box.pack_start(row, False, False, 0)

        scrolled, self.request_view = text_view(monospace=True, height=340)
        box.pack_start(scrolled, True, True, 0)
        return box

    # Actions

    def _on_fetch(self, _button):
        """
        Collect the sources of the first context in a worker thread.
        """
        request = self.plan.groups[0].request
        settings = self.settings
        self.fetch_button.set_sensitive(False)
        self.spinner.show()
        self.spinner.start()
        self.request_status.set_text(_("Collecting the Wikipedia sources..."))

        def progress(message: str):
            GLib.idle_add(self._set_status, _("Working: %s") % message,
                          priority=GLib.PRIORITY_DEFAULT)

        def work():
            try:
                pipeline = StoryPipeline(settings, should_stop=self.stop_event.is_set,
                                         progress=progress)
                result, error = pipeline.prepare(request), None
            except Exception as exc:  # noqa: BLE001 - reported to the user
                result, error = None, exc
            # Default priority: the spinner animation must not delay the result
            GLib.idle_add(self._on_fetched, result, error, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, text: str) -> bool:
        """
        Show a progress message (callable from GLib.idle_add).

        Parameters
        ----------
        text : str
            Message.

        Returns
        -------
        bool
            False, to run once.
        """
        if not self.closed:
            self.request_status.set_text(text)
        return False

    def _on_fetched(self, prepared, error: Optional[Exception]) -> bool:
        """
        Display the full request.

        Parameters
        ----------
        prepared : PreparedPrompt or None
            Prompts and sources.
        error : Exception or None
            Error.

        Returns
        -------
        bool
            False, to run once.
        """
        if self.closed:
            return False
        self.spinner.stop()
        self.spinner.hide()
        self.fetch_button.set_sensitive(True)
        if error is not None:
            self.request_status.set_text(_("Error: %s") % error)
            return False
        richness = _("rich (general knowledge only for daily life)") if prepared.sources.rich \
            else _("limited (marked general knowledge allowed)")
        self.request_status.set_text(_("Sources: %(count)d excerpt(s), %(richness)s.")
                                     % {"count": len(prepared.sources.extracts),
                                        "richness": richness})
        parts = []
        if prepared.sources.warnings:
            parts.append(_("Warnings:") + "\n" + "\n".join(
                "- %s" % w for w in prepared.sources.warnings))
        parts.append("===== %s =====\n%s" % (_("System prompt"), prepared.system))
        parts.append("===== %s =====\n%s" % (_("User message"), prepared.user))
        set_text(self.request_view, "\n\n".join(parts))
        return False

    def present(self):
        """
        Bring the dialog to the front.
        """
        self.dialog.present()

    def close(self):
        """
        Stop the collection and destroy the dialog.
        """
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        self.dialog.destroy()
