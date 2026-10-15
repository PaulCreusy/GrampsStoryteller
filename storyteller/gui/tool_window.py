"""
Main window of the tool, kept compact: which persons, which options, and the
progress of a run. What is sent and the result are shown in popups, only when
the user asks for them or at the end of a run.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import copy
import html
import threading
from typing import List, Optional

# Dependencies #
from gi.repository import GLib, Gtk
from gramps.gen.display.name import displayer as name_displayer
from gramps.gui.dialog import ErrorDialog, QuestionDialog2
from gramps.gui.managedwindow import ManagedWindow
from gramps.gui.plug import tool

# Local imports #
from ..batch import (
    MAX_CONSECUTIVE_ERRORS, SKIP_EXISTING, STATUS_ADDED, STATUS_FAILED, STATUS_NOT_WRITTEN,
    STATUS_REPLACED, BatchGroup, BatchPlan, BatchReport, PersonOutcome, build_plan, run_batch)
from ..config import Settings
from ..extraction import SKIP_LIVING, SKIP_NO_LANDMARK, SKIP_PRIVATE, extract_person
from ..i18n import _
from ..llm.registry import PRESETS
from ..models import GenerationResult, PersonFacts
from ..note_content import build_note_content
from ..notes import write_story_note
from ..pipeline import StoryPipeline
from ..selection import (
    GENERATION_SCOPES, SCOPE_ACTIVE, SCOPE_ALL, SCOPE_ANCESTORS, SCOPE_DESCENDANTS, SCOPE_FILTER,
    SCOPE_SELECTED, custom_filters, people_for_scope, persons_with_story_note)
from .result_dialog import ResultDialog, report_summary
from .sent_dialog import SentDataDialog
from .settings_dialog import edit_settings
from .widgets import framed, wrapped_label

#############
# Constants #
#############

RESPONSE_SETTINGS = 1
RESPONSE_SENT_DATA = 2
RESPONSE_GENERATE = 3
RESPONSE_STOP = 4

# Persons read between two refreshes of the interface
EVENTS_EVERY = 25

#############
# Functions #
#############


def _skip_reasons() -> dict:
    """
    Return the explanation of each skip reason.

    Returns
    -------
    dict
        Reason -> message.
    """
    return {
        SKIP_LIVING: _("probably alive: living persons are always excluded"),
        SKIP_PRIVATE: _("marked private"),
        SKIP_NO_LANDMARK: _("no usable birth, baptism, death or burial year or place"),
        SKIP_EXISTING: _("already has a note"),
    }


def _thousands(value: int) -> str:
    """
    Format a large number with a thin space every three digits.

    Parameters
    ----------
    value : int
        Number.

    Returns
    -------
    str
        Formatted number.
    """
    return "{:,}".format(value).replace(",", " ")


def _request_lines(request) -> List[str]:
    """
    Return the lines of a request as displayed.

    Parameters
    ----------
    request : ContextRequest
        Anonymized request.

    Returns
    -------
    list of str
        Landmarks and occupation.
    """
    lines = list(request.lines)
    if request.occupation:
        lines.append(_("Occupation: %s") % request.occupation)
    return lines


###########
# Classes #
###########


class StorytellerTool(tool.Tool, ManagedWindow):
    """
    Gramps tool generating the sourced historical context of persons.

    Parameters
    ----------
    dbstate : DbState
        Database state.
    user : User
        Gramps user object (gives access to the uistate).
    options_class : class
        Tool options class.
    name : str
        Tool name.
    callback : callable, optional
        Unused, required by the tool API.
    """

    def __init__(self, dbstate, user, options_class, name, callback=None):
        uistate = user.uistate
        self.label = _("Storyteller: historical context")
        tool.Tool.__init__(self, dbstate, options_class, name)
        ManagedWindow.__init__(self, uistate, [], self.__class__)

        self.dbstate = dbstate
        self.settings = Settings.load()
        self.facts_list: List[PersonFacts] = []
        self.existing_notes: set = set()
        self.filters: list = []
        self.stop_event = threading.Event()
        self.running = False
        self.collecting = False
        self.refresh_serial = 0
        self.refresh_timer = None
        self.pulse_id = None
        self.batch_report: Optional[BatchReport] = None
        self.batch_total = 0
        self.batch_done = 0
        self.sent_dialog: Optional[SentDataDialog] = None
        self.result_dialog: Optional[ResultDialog] = None

        self._build()
        self.set_window(self.dialog, None, self.label)
        self.setup_configs("interface.grampsstoryteller-compact", 620, 300)
        self.show()
        self._refresh()

    # Construction

    def _build(self):
        """
        Create the widgets.
        """
        self.dialog = Gtk.Dialog()
        self.settings_button = self.dialog.add_button(_("_Settings..."), RESPONSE_SETTINGS)
        self.sent_button = self.dialog.add_button(_("_Data sent..."), RESPONSE_SENT_DATA)
        self.sent_button.set_tooltip_text(_(
            "Shows the years and places sent for each person and, on demand, the full request "
            "with the Wikipedia excerpts. Nothing is sent to the LLM."))
        self.stop_button = self.dialog.add_button(_("_Stop"), RESPONSE_STOP)
        self.generate_button = self.dialog.add_button(_("_Generate"), RESPONSE_GENERATE)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.set_default_response(RESPONSE_GENERATE)
        self.dialog.connect("response", self._on_response)

        box = self.dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(10)
        box.pack_start(framed(_("Persons"), self._build_scope()), False, False, 0)
        box.pack_start(framed(_("Options"), self._build_options()), False, False, 0)
        box.pack_start(self._build_status(), False, False, 0)
        # Shown only when relevant, even when Gramps calls show_all()
        for widget in (self.generations_label, self.generations, self.filter_combo,
                       self.progress, self.result_button, self.stop_button,
                       self.status_label):
            widget.set_no_show_all(True)
        self.dialog.show_all()
        self._update_scope_widgets()
        self._update_correction_sensitivity()
        self._set_running(False)

    def _build_scope(self) -> Gtk.Widget:
        """
        Build the selection of the persons to process.

        Returns
        -------
        Gtk.Widget
            Scope widgets.
        """
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(spacing=8)
        self.scope_combo = Gtk.ComboBoxText()
        for scope_id, label in (
                (SCOPE_ACTIVE, _("Active person")),
                (SCOPE_SELECTED, _("Persons selected in the People view")),
                (SCOPE_ANCESTORS, _("Active person and their ancestors")),
                (SCOPE_DESCENDANTS, _("Active person and their descendants")),
                (SCOPE_FILTER, _("Custom filter")),
                (SCOPE_ALL, _("Whole tree"))):
            self.scope_combo.append(scope_id, label)
        self.scope_combo.set_active_id(SCOPE_ACTIVE)
        self.scope_combo.connect("changed", self._on_scope_changed)
        row.pack_start(self.scope_combo, False, False, 0)

        self.generations_label = Gtk.Label(label=_("Generations:"))
        self.generations = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.generations.set_value(4)
        self.generations.connect("value-changed", lambda _w: self._schedule_refresh())
        row.pack_start(self.generations_label, False, False, 0)
        row.pack_start(self.generations, False, False, 0)

        self.filter_combo = Gtk.ComboBoxText()
        self.filter_combo.connect("changed", lambda _w: self._schedule_refresh())
        row.pack_start(self.filter_combo, True, True, 0)

        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh",
                                                            Gtk.IconSize.BUTTON)
        self.refresh_button.set_tooltip_text(_(
            "Read the persons again (after changing the active person or the selection)."))
        self.refresh_button.connect("clicked", lambda _b: self._refresh())
        row.pack_end(self.refresh_button, False, False, 0)
        outer.pack_start(row, False, False, 0)

        self.summary_label = wrapped_label()
        outer.pack_start(self.summary_label, False, False, 0)

        self.skip_existing_check = Gtk.CheckButton(
            label=_("Skip the persons who already have a Storyteller note"))
        self.skip_existing_check.connect("toggled", lambda _w: self._show_plan())
        outer.pack_start(self.skip_existing_check, False, False, 0)
        return outer

    def _build_options(self) -> Gtk.Widget:
        """
        Build the options of the run.

        Returns
        -------
        Gtk.Widget
            Options widgets.
        """
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grid = Gtk.Grid(column_spacing=18, row_spacing=4)
        self.occupation_check = Gtk.CheckButton(label=_("Send the occupation"))
        self.occupation_check.set_active(bool(self.settings["include_occupation"]))
        self.occupation_check.connect("toggled", self._on_occupation_toggled)
        self.no_cache_check = Gtk.CheckButton(label=_("Regenerate (ignore the cache)"))
        self.no_cache_check.set_tooltip_text(_(
            "By default, a context already generated with the same sources and prompts is "
            "reused without calling the LLM."))
        self.validation_check = Gtk.CheckButton(label=_("LLM validation"))
        self.validation_check.set_active(bool(self.settings["llm_validation"]))
        self.validation_check.connect("toggled", self._on_validation_toggled)
        self.validation_check.set_tooltip_text(
            _("Checks the text with a second request (costs more tokens)."))
        self.correction_check = Gtk.CheckButton(label=_("Automatic correction"))
        self.correction_check.set_active(bool(self.settings["auto_correction"]))
        self.correction_check.connect("toggled", self._on_correction_toggled)
        self.correction_check.set_tooltip_text(_(
            "When the validation finds problems, the text is rewritten without them, then "
            "checked again (two more requests)."))
        grid.attach(self.occupation_check, 0, 0, 1, 1)
        grid.attach(self.no_cache_check, 0, 1, 1, 1)
        grid.attach(self.validation_check, 1, 0, 1, 1)
        grid.attach(self.correction_check, 1, 1, 1, 1)
        outer.pack_start(grid, False, False, 0)

        self.model_label = wrapped_label()
        self.model_label.get_style_context().add_class("dim-label")
        outer.pack_start(self.model_label, False, False, 0)
        self._update_model_label()
        return outer

    def _build_status(self) -> Gtk.Widget:
        """
        Build the progress and status line.

        Returns
        -------
        Gtk.Widget
            Status widgets.
        """
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.progress = Gtk.ProgressBar()
        outer.pack_start(self.progress, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.status_label = wrapped_label()
        self.result_button = Gtk.Button(label=_("Show the result"))
        self.result_button.connect("clicked", lambda _b: self._show_result())
        row.pack_start(self.status_label, True, True, 0)
        row.pack_end(self.result_button, False, False, 0)
        outer.pack_start(row, False, False, 0)
        return outer

    def build_menu_names(self, obj):
        """
        Return the names shown in the Gramps window menu.

        Returns
        -------
        tuple
            Title and submenu name.
        """
        return (self.label, None)

    # State

    def _update_model_label(self):
        """
        Show the configured provider and model.
        """
        provider_id = self.settings["provider"]
        preset = PRESETS.get(provider_id, PRESETS["custom"])
        model = self.settings.provider_setting(provider_id, "model") or "?"
        self.model_label.set_text(_("Model: %(provider)s · %(model)s — text language: %(lang)s")
                                  % {"provider": preset.label, "model": model,
                                     "lang": self.settings["language"]})

    def _update_correction_sensitivity(self):
        """
        Allow the automatic correction only with the LLM validation.
        """
        self.correction_check.set_sensitive(self.validation_check.get_active())

    def _update_scope_widgets(self):
        """
        Show the widgets of the selected scope.
        """
        scope = self.scope_combo.get_active_id()
        self.generations_label.set_visible(scope in GENERATION_SCOPES)
        self.generations.set_visible(scope in GENERATION_SCOPES)
        self.filter_combo.set_visible(scope == SCOPE_FILTER)

    def _has_work(self) -> bool:
        """
        Tell whether at least one person can be processed.

        Returns
        -------
        bool
            True when the plan has a context to generate.
        """
        return bool(self.facts_list) and bool(self._plan().groups)

    def _set_running(self, running: bool):
        """
        Enable or disable the actions while a run or a reading goes on.

        Parameters
        ----------
        running : bool
            Whether a run is going on.
        """
        self.running = running
        busy = running or self.collecting
        for widget in (self.settings_button, self.scope_combo, self.refresh_button,
                       self.generations, self.filter_combo, self.skip_existing_check,
                       self.occupation_check, self.validation_check, self.no_cache_check):
            widget.set_sensitive(not busy)
        self.correction_check.set_sensitive(not busy and self.validation_check.get_active())
        work = not busy and self._has_work()
        self.generate_button.set_sensitive(work)
        self.sent_button.set_sensitive(not busy and bool(self.facts_list))
        self.stop_button.set_sensitive(running)
        self.stop_button.set_visible(running)
        if running:
            self.stop_event.clear()
            self.progress.set_fraction(0.0)
            self.progress.show()
        elif not self.collecting:
            self._stop_pulse()
            self.progress.hide()

    def _stop_pulse(self):
        """
        Stop the progress bar animation.
        """
        if self.pulse_id is not None:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = None

    def _start_pulse(self):
        """
        Animate the progress bar when the progress is unknown.
        """
        if self.pulse_id is None:
            self.pulse_id = GLib.timeout_add(120, self._pulse)

    def _pulse(self) -> bool:
        """
        Animate the progress bar.

        Returns
        -------
        bool
            True to keep the timer.
        """
        self.progress.pulse()
        return True

    def _set_status(self, text: str) -> bool:
        """
        Show a status message (callable from GLib.idle_add).

        Parameters
        ----------
        text : str
            Message.

        Returns
        -------
        bool
            False, to run once.
        """
        if self._is_open():
            self.status_label.set_text(text)
            self.status_label.set_visible(bool(text))
        return False

    def _is_open(self) -> bool:
        """
        Tell whether the window is still displayed.

        Returns
        -------
        bool
            True when it is.
        """
        return bool(getattr(self, "opened", False)) and self.window is not None

    # Persons

    def _on_scope_changed(self, _combo):
        """
        Update the widgets and the persons of the new scope.
        """
        scope = self.scope_combo.get_active_id()
        if scope == SCOPE_FILTER and not self.filters:
            self._load_filters()
        self._update_scope_widgets()
        self._refresh()

    def _load_filters(self):
        """
        Fill the custom filter list.
        """
        try:
            self.filters = custom_filters()
        except Exception:  # noqa: BLE001 - a broken filter file must not break the tool
            self.filters = []
        self.filter_combo.remove_all()
        for index, person_filter in enumerate(self.filters):
            self.filter_combo.append(str(index), person_filter.get_name())
        if self.filters:
            self.filter_combo.set_active(0)

    def _selected_filter(self):
        """
        Return the selected custom filter.

        Returns
        -------
        GenericFilter or None
            Filter.
        """
        active = self.filter_combo.get_active()
        return self.filters[active] if 0 <= active < len(self.filters) else None

    def _schedule_refresh(self):
        """
        Refresh the persons shortly after the last change of a setting.
        """
        if self.refresh_timer is not None:
            GLib.source_remove(self.refresh_timer)
        self.refresh_timer = GLib.timeout_add(400, self._timed_refresh)

    def _timed_refresh(self) -> bool:
        """
        Run a scheduled refresh.

        Returns
        -------
        bool
            False, to run once.
        """
        self.refresh_timer = None
        if self._is_open():
            self._refresh()
        return False

    def _empty_scope_message(self, scope: str) -> str:
        """
        Explain why a scope has no person.

        Parameters
        ----------
        scope : str
            Scope.

        Returns
        -------
        str
            Message.
        """
        if scope == SCOPE_SELECTED:
            return _("No person selected: select persons in the People view, then click "
                     "\"Refresh\".")
        if scope == SCOPE_FILTER:
            if not self.filters:
                return _("No custom person filter: create one with Edit ▸ Person Filter "
                         "Editor in the People view.")
            return _("No person matches this filter.")
        if scope == SCOPE_ALL:
            return _("The tree has no person.")
        return _("No active person: select a person in the People view, then click "
                 "\"Refresh\".")

    def _refresh(self):
        """
        Read the persons of the scope and summarize what will be done.

        The tree is read in the GTK main loop (the database is not shared
        with the worker thread), refreshing the interface regularly.
        """
        if self.running:
            return
        self.refresh_serial += 1
        serial = self.refresh_serial
        scope = self.scope_combo.get_active_id()
        try:
            handles = people_for_scope(self.db, self.uistate, scope,
                                       int(self.generations.get_value()),
                                       self._selected_filter())
        except Exception as exc:  # noqa: BLE001 - e.g. a broken custom filter
            handles = []
            self._set_status(_("Error: %s") % exc)

        self.collecting = True
        self._set_running(False)
        facts_list = []
        for index, handle in enumerate(handles):
            person = self.db.get_person_from_handle(handle)
            if person is not None:
                facts_list.append(extract_person(self.db, person,
                                                 name_displayer.display(person),
                                                 include_occupation=True))
            if index % EVENTS_EVERY == EVENTS_EVERY - 1:
                self.progress.show()
                self.progress.set_fraction((index + 1) / len(handles))
                self._set_status(_("Reading the persons: %(done)d / %(total)d")
                                 % {"done": index + 1, "total": len(handles)})
                while Gtk.events_pending():
                    Gtk.main_iteration()
                if not self._is_open() or serial != self.refresh_serial:
                    return
        self.existing_notes = persons_with_story_note(self.db, handles)
        self.facts_list = facts_list
        self.collecting = False
        # Keep the summary of the last run next to its "Show the result" button
        self._set_status(" ".join(report_summary(self.batch_report, MAX_CONSECUTIVE_ERRORS))
                         if self.batch_report is not None and self.batch_report.outcomes
                         else "")
        if not facts_list:
            self.summary_label.set_markup("<i>%s</i>" % html.escape(
                self._empty_scope_message(scope)))
        else:
            self._show_plan()
        self._set_running(False)

    def _plan(self, settings: Optional[Settings] = None) -> BatchPlan:
        """
        Group the persons of the scope by anonymized request.

        Parameters
        ----------
        settings : Settings, optional
            Settings of the run; the current options by default.

        Returns
        -------
        BatchPlan
            Plan.
        """
        settings = settings or self._run_settings()
        return build_plan(self.facts_list, settings["language"],
                          settings["include_occupation"], self.existing_notes,
                          self.skip_existing_check.get_active())

    def _show_plan(self):
        """
        Summarize, in one or two lines, the persons that will be processed.
        """
        if self.collecting or not self.facts_list:
            return
        plan = self._plan()
        reasons = _skip_reasons()
        if len(plan.items) == 1:
            item = plan.items[0]
            name = "<b>%s</b> [%s]" % (html.escape(item.facts.name),
                                       html.escape(item.facts.gramps_id))
            if item.skip_reason:
                detail = _("will not be processed: %s") % reasons.get(item.skip_reason,
                                                                       item.skip_reason)
            elif item.has_note:
                detail = _("its note will be replaced")
            else:
                detail = _("a note will be added")
            self.summary_label.set_markup("%s — %s" % (name, html.escape(detail)))
        else:
            to_process = len(plan.to_process)
            parts = [_("%(count)d persons, %(process)d to process.")
                     % {"count": len(plan.items), "process": to_process}]
            replaced = sum(1 for item in plan.to_process if item.has_note)
            if replaced:
                parts.append(_("%d note(s) will be replaced.") % replaced)
            skipped = sum(plan.skipped().values())
            if skipped:
                parts.append(_("%d skipped (living, private, without landmark or with a "
                               "note): see \"Data sent\".") % skipped)
            self.summary_label.set_markup("<b>%s</b> %s" % (
                html.escape(parts[0]), html.escape(" ".join(parts[1:]))))
        self.generate_button.set_sensitive(not self.running and bool(plan.groups))

    # Options

    def _on_occupation_toggled(self, button: Gtk.CheckButton):
        """
        Remember the occupation option and refresh the summary.

        Parameters
        ----------
        button : Gtk.CheckButton
            Occupation check box.
        """
        self._remember("include_occupation", button.get_active())
        self._show_plan()

    def _on_validation_toggled(self, button: Gtk.CheckButton):
        """
        Remember the LLM validation option.

        Parameters
        ----------
        button : Gtk.CheckButton
            Validation check box.
        """
        self._remember("llm_validation", button.get_active())
        self._update_correction_sensitivity()

    def _on_correction_toggled(self, button: Gtk.CheckButton):
        """
        Remember the automatic correction option.

        Parameters
        ----------
        button : Gtk.CheckButton
            Correction check box.
        """
        self._remember("auto_correction", button.get_active())

    def _remember(self, key: str, value: bool):
        """
        Save an option changed in the window.

        Parameters
        ----------
        key : str
            Setting name.
        value : bool
            New value.
        """
        if self.settings[key] == value:
            return
        self.settings[key] = value
        try:
            self.settings.save()
        except OSError:
            pass

    def _run_settings(self) -> Settings:
        """
        Return a copy of the settings with the window options applied.

        Returns
        -------
        Settings
            Settings for one run.
        """
        settings = copy.deepcopy(self.settings)
        settings["include_occupation"] = self.occupation_check.get_active()
        settings["llm_validation"] = self.validation_check.get_active()
        settings["auto_correction"] = (self.validation_check.get_active()
                                       and self.correction_check.get_active())
        return settings

    # Actions

    def _on_response(self, _dialog, response_id: int):
        """
        Dispatch the dialog buttons.

        Parameters
        ----------
        response_id : int
            Button response.
        """
        if response_id == RESPONSE_SETTINGS:
            if edit_settings(self.window, self.settings):
                self.occupation_check.set_active(bool(self.settings["include_occupation"]))
                self.validation_check.set_active(bool(self.settings["llm_validation"]))
                self.correction_check.set_active(bool(self.settings["auto_correction"]))
                self._update_model_label()
                self._show_plan()
        elif response_id == RESPONSE_SENT_DATA:
            self._show_sent_data()
        elif response_id == RESPONSE_GENERATE:
            self._start_generation()
        elif response_id == RESPONSE_STOP:
            self.stop_event.set()
            self._set_status(_("Stopping after the current request..."))
        elif response_id == Gtk.ResponseType.CLOSE:
            # The window manager close button is handled by ManagedWindow
            self.close()

    def _show_sent_data(self):
        """
        Open the popup with the landmarks and the full request.
        """
        if self.collecting or not self.facts_list:
            return
        if self.sent_dialog is not None:
            self.sent_dialog.close()
        self.sent_dialog = SentDataDialog(self.window, self._plan(), self._run_settings(),
                                          _skip_reasons(), _request_lines)

    def _show_result(self):
        """
        Open the popup with the result of the last run.
        """
        if self.batch_report is None or not self.batch_report.outcomes:
            return
        if self.result_dialog is not None:
            self.result_dialog.close()
        self.result_dialog = ResultDialog(self.window, self.batch_report,
                                          MAX_CONSECUTIVE_ERRORS)

    def _confirm_batch(self, plan: BatchPlan, settings: Settings) -> bool:
        """
        Show the estimated cost of a batch and ask for confirmation.

        Parameters
        ----------
        plan : BatchPlan
            Plan to run.
        settings : Settings
            Settings of the run.

        Returns
        -------
        bool
            True to start.
        """
        estimate = plan.estimate(settings["llm_validation"], settings["auto_correction"],
                                 int(settings["length_words"]))
        lines = [
            _("%(persons)d persons will receive a note (%(replaced)d replaced).")
            % {"persons": estimate.persons, "replaced": estimate.replaced},
            _("%(groups)d distinct context(s): at most %(requests)d LLM requests, about "
              "%(input)s input tokens and %(output)s output tokens.")
            % {"groups": estimate.groups, "requests": estimate.llm_requests,
               "input": _thousands(estimate.input_tokens),
               "output": _thousands(estimate.output_tokens)},
            _("These are upper bounds: the cache avoids the requests already made, and "
              "reasoning models may use more output tokens."),
            _("Each note is written in its own transaction and can be undone. The batch can "
              "be stopped at any time."),
        ]
        return QuestionDialog2(_("Generate %d notes?") % estimate.persons,
                               html.escape("\n\n".join(lines)), _("_Generate"), _("_Cancel"),
                               parent=self.window).run()

    def _start_generation(self):
        """
        Generate the notes of the persons of the scope in a worker thread.
        """
        if self.collecting or self.running:
            return
        if self.db.readonly:
            ErrorDialog(_("Read-only tree"), _("The family tree is opened read-only."),
                        parent=self.window)
            return
        settings = self._run_settings()
        plan = self._plan(settings)
        if not plan.groups:
            self._set_status(_("Nothing to send for these persons."))
            return
        if len(plan.to_process) > 1 and not self._confirm_batch(plan, settings):
            return
        use_cache = not self.no_cache_check.get_active()
        lang = settings["language"]
        self.batch_report = BatchReport()
        self.batch_total = len(plan.groups)
        self.batch_done = 0
        single = len(plan.groups) == 1

        def progress(message: str):
            GLib.idle_add(self._set_status, _("Working: %s") % message)

        def on_start(index: int, group: BatchGroup):
            GLib.idle_add(self._set_status, _("Context %(index)d / %(total)d: %(name)s")
                          % {"index": index + 1, "total": len(plan.groups),
                             "name": group.items[0].facts.name})

        def on_done(group: BatchGroup, result: Optional[GenerationResult],
                    error: Optional[Exception]):
            GLib.idle_add(self._on_group_done, group, result, error, lang)

        def work():
            try:
                pipeline = StoryPipeline(settings, should_stop=self.stop_event.is_set,
                                         progress=progress)
                report = run_batch(plan, lambda request: pipeline.generate(request, use_cache),
                                   on_done, self.stop_event.is_set, on_start)
                error = None
            except Exception as exc:  # noqa: BLE001 - reported to the user
                report, error = None, exc
            GLib.idle_add(self._on_batch_done, report, error)

        if self.result_dialog is not None:
            self.result_dialog.close()
            self.result_dialog = None
        self.result_button.hide()
        self._set_running(True)
        if single:
            self._start_pulse()
        self._set_status(_("Collecting the Wikipedia sources..."))
        threading.Thread(target=work, daemon=True).start()

    def _on_group_done(self, group: BatchGroup, result: Optional[GenerationResult],
                       error: Optional[Exception], lang: str) -> bool:
        """
        Write the notes of a group, in the GTK main loop.

        Parameters
        ----------
        group : BatchGroup
            Processed group.
        result : GenerationResult or None
            Generation result.
        error : Exception or None
            Generation error.
        lang : str
            Language of the text.

        Returns
        -------
        bool
            False, to run once.
        """
        if not self._is_open():
            return False
        report = self.batch_report
        self.batch_done += 1
        if self.pulse_id is None:
            self.progress.set_fraction(self.batch_done / max(self.batch_total, 1))

        if error is not None:
            for item in group.items:
                report.add(PersonOutcome(name=item.facts.name, gramps_id=item.facts.gramps_id,
                                         status=STATUS_FAILED, message=_("Error: %s") % error))
            return False

        validation = result.validation
        warnings = result.sources.warnings + validation.warnings
        content = None
        if validation.ok:
            content = build_note_content(result.text, result.sources.extracts,
                                         result.provider_label, result.model, lang)
        for item in group.items:
            facts = item.facts
            outcome = PersonOutcome(
                name=facts.name, gramps_id=facts.gramps_id, status=STATUS_NOT_WRITTEN,
                warnings=list(warnings), corrected=list(validation.corrected_problems),
                text=content.text if content is not None else result.text,
                original_text=result.original_text, from_cache=result.from_cache,
                llm_checked=validation.llm_checked, llm_problems=validation.llm_problems)
            if content is None:
                outcome.message = "; ".join(validation.errors)
            else:
                try:
                    _handle, replaced = write_story_note(self.db, facts.handle, content,
                                                         facts.name)
                    outcome.status = STATUS_REPLACED if replaced else STATUS_ADDED
                except Exception as exc:  # noqa: BLE001 - reported to the user
                    outcome.status = STATUS_FAILED
                    outcome.message = _("Error while writing the note: %s") % exc
            report.add(outcome)
        return False

    def _on_batch_done(self, run_report: Optional[BatchReport],
                       error: Optional[Exception]) -> bool:
        """
        End a run and open the result popup, in the GTK main loop.

        Parameters
        ----------
        run_report : BatchReport or None
            Cancellation and abort information.
        error : Exception or None
            Error that stopped the run before the first group.

        Returns
        -------
        bool
            False, to run once.
        """
        if not self._is_open():
            return False
        self._set_running(False)
        report = self.batch_report
        if error is not None:
            self._set_status(_("Failed."))
            ErrorDialog(_("The generation failed"), str(error), parent=self.window)
            return False
        report.cancelled = run_report.cancelled
        report.aborted = run_report.aborted
        self._set_status(" ".join(report_summary(report, MAX_CONSECUTIVE_ERRORS)))
        self._refresh_notes()
        self._show_plan()
        if report.outcomes:
            self.result_button.show()
            self._show_result()
        return False

    def _refresh_notes(self):
        """
        Update the persons who now have a note.
        """
        handles = [facts.handle for facts in self.facts_list]
        self.existing_notes = persons_with_story_note(self.db, handles)

    def close(self, *obj):
        """
        Stop the running request and close the window and its popups.
        """
        self.stop_event.set()
        self.refresh_serial += 1
        self._stop_pulse()
        if self.refresh_timer is not None:
            GLib.source_remove(self.refresh_timer)
            self.refresh_timer = None
        for popup in (self.sent_dialog, self.result_dialog):
            if popup is not None:
                popup.close()
        ManagedWindow.close(self, *obj)


class StorytellerOptions(tool.ToolOptions):
    """
    Options of the tool (none: everything is in the settings dialog).

    Parameters
    ----------
    name : str
        Tool name.
    person_id : str, optional
        Unused.
    """

    def __init__(self, name, person_id=None):
        tool.ToolOptions.__init__(self, name, person_id)
