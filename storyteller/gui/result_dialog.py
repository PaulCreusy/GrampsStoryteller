"""
Popup showing the result of a run: a summary, the list of the processed
persons and the details (warnings and note text) of the selected one.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import List

# Dependencies #
from gi.repository import GLib, Gtk, Pango

# Local imports #
from ..batch import (
    STATUS_ADDED, STATUS_FAILED, STATUS_NOT_WRITTEN, STATUS_REPLACED, BatchReport, PersonOutcome)
from ..i18n import _
from .widgets import set_text, text_view, wrapped_label

#############
# Constants #
#############

COLUMN_SYMBOL, COLUMN_NAME, COLUMN_STATUS, COLUMN_WARNINGS, COLUMN_INDEX = range(5)

#############
# Functions #
#############


def status_label(status: str) -> str:
    """
    Return the displayed label of an outcome status.

    Parameters
    ----------
    status : str
        Outcome status.

    Returns
    -------
    str
        Label.
    """
    return {
        STATUS_ADDED: _("note added"),
        STATUS_REPLACED: _("note replaced"),
        STATUS_NOT_WRITTEN: _("note not written"),
        STATUS_FAILED: _("error"),
    }.get(status, status)


def outcome_details(outcome: PersonOutcome) -> str:
    """
    Describe the outcome of a person: status, checks and text.

    Parameters
    ----------
    outcome : PersonOutcome
        Outcome.

    Returns
    -------
    str
        Text of the details pane.
    """
    lines = ["%s [%s] — %s" % (outcome.name, outcome.gramps_id, status_label(outcome.status))]
    if outcome.message:
        lines.append(outcome.message)
    if outcome.from_cache:
        lines.append(_("Text taken from the cache (tick \"Regenerate\" to call the LLM)."))
    if outcome.corrected:
        lines.append("")
        lines.append(_("Automatic correction: the text was rewritten to fix these problems:"))
        lines.extend("- %s" % problem for problem in outcome.corrected)
    if outcome.warnings:
        lines.append("")
        lines.append(_("Warnings after correction:") if outcome.corrected else _("Warnings:"))
        lines.extend("- %s" % warning for warning in outcome.warnings)
    if outcome.llm_checked and not outcome.llm_problems:
        lines.append("")
        lines.append(_("LLM validation: no problem found."))
    if outcome.text:
        lines.append("")
        lines.append("===== %s =====" % (_("Note") if outcome.written else _("Generated text")))
        lines.append(outcome.text)
    if outcome.original_text:
        lines.append("")
        lines.append("===== %s =====" % _("Text before the automatic correction"))
        lines.append(outcome.original_text)
    return "\n".join(lines)


def report_summary(report: BatchReport, max_errors: int) -> List[str]:
    """
    Summarize a run in a few sentences.

    Parameters
    ----------
    report : BatchReport
        Run report.
    max_errors : int
        Failures in a row that stop a batch.

    Returns
    -------
    list of str
        Sentences.
    """
    parts = []
    written = report.created + report.replaced
    if written:
        parts.append(_("%(written)d note(s) written (%(created)d added, %(replaced)d "
                       "replaced).") % {"written": written, "created": report.created,
                                        "replaced": report.replaced})
    if report.with_warnings:
        parts.append(_("%d with warnings: review them.") % report.with_warnings)
    if report.not_written:
        parts.append(_("%d not written (the text failed the checks).") % report.not_written)
    if report.failed:
        parts.append(_("%d error(s).") % report.failed)
    if report.aborted:
        parts.append(_("Stopped after %(count)d failures in a row: %(error)s")
                     % {"count": max_errors, "error": report.aborted})
    elif report.cancelled:
        parts.append(_("Stopped by the user: the remaining persons were not processed."))
    if not parts:
        parts.append(_("No person was processed."))
    return parts


###########
# Classes #
###########


class ResultDialog:
    """
    Non-modal dialog with the result of a run.

    Parameters
    ----------
    parent : Gtk.Window
        Parent window.
    report : BatchReport
        Run report.
    max_errors : int
        Failures in a row that stop a batch.
    """

    def __init__(self, parent: Gtk.Window, report: BatchReport, max_errors: int):
        self.outcomes = report.outcomes
        self.closed = False
        single = len(self.outcomes) == 1

        self.dialog = Gtk.Dialog(title=_("Storyteller result"), transient_for=parent,
                                 modal=False)
        self.dialog.set_default_size(760, 620 if not single else 560)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", lambda *_a: self.close())

        area = self.dialog.get_content_area()
        area.set_spacing(8)
        area.set_border_width(10)
        summary = wrapped_label()
        summary.set_markup("<b>%s</b>" % GLib.markup_escape_text(" ".join(report_summary(report, max_errors))))
        area.pack_start(summary, False, False, 0)

        scrolled_details, self.details = text_view(height=260)
        if single:
            area.pack_start(scrolled_details, True, True, 0)
        else:
            self.only_problems = Gtk.CheckButton(
                label=_("Show only the persons with warnings or errors"))
            self.only_problems.connect("toggled", lambda _w: self._fill())
            area.pack_start(self.only_problems, False, False, 0)
            paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
            paned.pack1(self._build_list(), True, False)
            paned.pack2(scrolled_details, True, False)
            paned.set_position(220)
            area.pack_start(paned, True, True, 0)
            self._fill()
        if single:
            set_text(self.details, outcome_details(self.outcomes[0]))
        self.dialog.show_all()

    def _build_list(self) -> Gtk.Widget:
        """
        Build the list of the processed persons.

        Returns
        -------
        Gtk.Widget
            Scrolled list.
        """
        self.store = Gtk.ListStore(str, str, str, str, int)
        self.tree = Gtk.TreeView(model=self.store)
        columns = (("", COLUMN_SYMBOL, False), (_("Person"), COLUMN_NAME, True),
                   (_("Result"), COLUMN_STATUS, False), (_("Warnings"), COLUMN_WARNINGS, False))
        for title, column_id, expand in columns:
            renderer = Gtk.CellRendererText()
            if column_id == COLUMN_NAME:
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=column_id)
            column.set_expand(expand)
            column.set_resizable(True)
            self.tree.append_column(column)
        self.tree.get_selection().connect("changed", self._on_selection)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.tree)
        return scrolled

    def _fill(self):
        """
        Fill the list, keeping only the problems when asked.
        """
        only_problems = self.only_problems.get_active()
        self.store.clear()
        for index, outcome in enumerate(self.outcomes):
            if only_problems and outcome.written and not outcome.warnings:
                continue
            symbol = ("⚠" if outcome.warnings else "✓") if outcome.written else "✗"
            self.store.append([symbol, "%s [%s]" % (outcome.name, outcome.gramps_id),
                               status_label(outcome.status),
                               str(len(outcome.warnings)) if outcome.warnings else "",
                               index])
        if len(self.store):
            self.tree.get_selection().select_path(Gtk.TreePath.new_first())
        else:
            set_text(self.details, "")

    def _on_selection(self, selection: Gtk.TreeSelection):
        """
        Show the details of the selected person.

        Parameters
        ----------
        selection : Gtk.TreeSelection
            List selection.
        """
        model, tree_iter = selection.get_selected()
        if tree_iter is not None:
            set_text(self.details, outcome_details(self.outcomes[model[tree_iter][COLUMN_INDEX]]))

    def present(self):
        """
        Bring the dialog to the front.
        """
        self.dialog.present()

    def close(self):
        """
        Destroy the dialog.
        """
        if not self.closed:
            self.closed = True
            self.dialog.destroy()
