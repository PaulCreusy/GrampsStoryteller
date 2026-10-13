"""
Main window of the tool: selects the persons, shows what will be sent,
previews the full request and generates the notes, for one person or a
batch.
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
    MAX_CONSECUTIVE_ERRORS, SKIP_EXISTING, BatchGroup, BatchPlan, BatchReport, build_plan,
    run_batch)
from ..config import Settings
from ..extraction import SKIP_LIVING, SKIP_NO_LANDMARK, SKIP_PRIVATE, extract_person
from ..http import Cancelled
from ..i18n import _
from ..llm.registry import PRESETS
from ..models import GenerationResult, PersonFacts
from ..note_content import build_note_content
from ..notes import write_story_note
from ..pipeline import StoryPipeline
from ..selection import (
    GENERATION_SCOPES, SCOPE_ACTIVE, SCOPE_ALL, SCOPE_ANCESTORS, SCOPE_DESCENDANTS, SCOPE_FILTER,
    SCOPE_SELECTED, custom_filters, people_for_scope, persons_with_story_note)
from .settings_dialog import edit_settings
from .widgets import framed, set_text, text_view, wrapped_label

#############
# Constants #
#############

RESPONSE_SETTINGS = 1
RESPONSE_PREVIEW = 2
RESPONSE_GENERATE = 3
RESPONSE_STOP = 4

# Persons listed in the landmarks view of a batch
MAX_LISTED_PERSONS = 300
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
        self.batch_log: List[str] = []

        self._build()
        self.set_window(self.dialog, None, self.label)
        self.setup_configs("interface.grampsstoryteller", 760, 700)
        self.show()
        self._refresh()

    # Construction

    def _build(self):
        """
        Create the widgets.
        """
        self.dialog = Gtk.Dialog()
        self.settings_button = self.dialog.add_button(_("_Settings..."), RESPONSE_SETTINGS)
        self.preview_button = self.dialog.add_button(_("_Preview the request"), RESPONSE_PREVIEW)
        self.stop_button = self.dialog.add_button(_("_Stop"), RESPONSE_STOP)
        self.generate_button = self.dialog.add_button(_("_Generate"), RESPONSE_GENERATE)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", self._on_response)

        box = self.dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(8)
        box.pack_start(framed(_("Persons"), self._build_scope()), False, False, 0)

        sent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled, self.sent_view = text_view(monospace=True, height=110)
        sent_box.pack_start(wrapped_label(_(
            "Only the years, places and occupations (and the matching Wikipedia excerpts) are "
            "sent. No name, no identifier and no event type ever leave Gramps.")),
            False, False, 0)
        sent_box.pack_start(scrolled, True, True, 0)
        box.pack_start(framed(_("Anonymized landmarks"), sent_box), True, True, 0)

        options = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12,
                              max_children_per_line=4)
        self.occupation_check = Gtk.CheckButton(label=_("Send the occupation"))
        self.occupation_check.set_active(bool(self.settings["include_occupation"]))
        self.occupation_check.connect("toggled", self._on_occupation_toggled)
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
        self.no_cache_check = Gtk.CheckButton(label=_("Regenerate (ignore the cache)"))
        for widget in (self.occupation_check, self.validation_check, self.correction_check,
                       self.no_cache_check):
            options.add(widget)
        box.pack_start(options, False, False, 0)
        self._update_correction_sensitivity()

        self.model_label = wrapped_label()
        box.pack_start(self.model_label, False, False, 0)
        self._update_model_label()

        self.progress = Gtk.ProgressBar(show_text=False)
        self.status_label = wrapped_label()
        box.pack_start(self.progress, False, False, 0)
        box.pack_start(self.status_label, False, False, 0)

        scrolled, self.result_view = text_view(height=200)
        box.pack_start(framed(_("Result"), scrolled), True, True, 0)
        self.dialog.show_all()
        self._update_scope_widgets()
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

        refresh = Gtk.Button(label=_("Refresh"))
        refresh.set_tooltip_text(_("Read the persons again (after changing the active person or "
                                   "the selection)."))
        refresh.connect("clicked", lambda _b: self._refresh())
        row.pack_end(refresh, False, False, 0)
        self.refresh_button = refresh
        outer.pack_start(row, False, False, 0)

        self.skip_existing_check = Gtk.CheckButton(
            label=_("Skip the persons who already have a Storyteller note"))
        self.skip_existing_check.connect("toggled", lambda _w: self._show_plan())
        outer.pack_start(self.skip_existing_check, False, False, 0)

        self.summary_label = wrapped_label()
        outer.pack_start(self.summary_label, False, False, 0)
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

    def _set_running(self, running: bool):
        """
        Enable or disable the actions while a request runs.

        Parameters
        ----------
        running : bool
            Whether a request is running.
        """
        self.running = running
        busy = running or self.collecting
        for widget in (self.settings_button, self.preview_button, self.generate_button,
                       self.scope_combo, self.refresh_button, self.generations,
                       self.filter_combo, self.skip_existing_check):
            widget.set_sensitive(not busy)
        self.stop_button.set_sensitive(running)
        if running:
            self.stop_event.clear()
            self.progress.set_fraction(0.0)
            if self.pulse_id is None:
                self.pulse_id = GLib.timeout_add(120, self._pulse)
        else:
            self._stop_pulse()
            self.progress.set_fraction(0.0)

    def _stop_pulse(self):
        """
        Stop the progress bar animation.
        """
        if self.pulse_id is not None:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = None

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
        if self.window is not None:
            self.status_label.set_text(text)
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
        Read the persons of the scope and show what will be sent.

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
        self._set_running(False)
        self._set_status("")
        if not facts_list:
            self.summary_label.set_markup("<b>%s</b>" % html.escape(
                self._empty_scope_message(scope)))
            set_text(self.sent_view, "")
            return
        self._show_plan()

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
        Display the persons, what will be sent for each and a summary.
        """
        if self.collecting or not self.facts_list:
            return
        plan = self._plan()
        reasons = _skip_reasons()
        if len(plan.items) == 1:
            item = plan.items[0]
            facts = item.facts
            self.summary_label.set_markup("<b>%s</b> [%s]%s" % (
                html.escape(facts.name), html.escape(facts.gramps_id),
                html.escape(" — " + _("a note already exists and will be replaced"))
                if item.has_note and not item.skip_reason else ""))
            if item.skip_reason:
                set_text(self.sent_view, _("Nothing will be sent: %s.")
                         % reasons.get(item.skip_reason, item.skip_reason))
                return
            set_text(self.sent_view, "\n".join(self._request_lines(item.request)))
            return

        skipped = plan.skipped()
        parts = [_("%(count)d persons: %(process)d to process in %(groups)d distinct "
                   "context(s).") % {"count": len(plan.items),
                                     "process": len(plan.to_process),
                                     "groups": len(plan.groups)}]
        for reason, count in sorted(skipped.items()):
            parts.append(_("%(count)d skipped (%(reason)s).")
                         % {"count": count, "reason": reasons.get(reason, reason)})
        replaced = sum(1 for item in plan.to_process if item.has_note)
        if replaced:
            parts.append(_("%d existing note(s) will be replaced.") % replaced)
        self.summary_label.set_text(" ".join(parts))

        lines = []
        for item in plan.items[:MAX_LISTED_PERSONS]:
            lines.append("%s [%s]" % (item.facts.name, item.facts.gramps_id))
            if item.skip_reason:
                lines.append("    — %s" % reasons.get(item.skip_reason, item.skip_reason))
            else:
                lines.extend("    %s" % line for line in self._request_lines(item.request))
        if len(plan.items) > MAX_LISTED_PERSONS:
            lines.append(_("... and %d more persons.") % (len(plan.items) - MAX_LISTED_PERSONS))
        set_text(self.sent_view, "\n".join(lines))

    @staticmethod
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

    # Options

    def _on_occupation_toggled(self, button: Gtk.CheckButton):
        """
        Remember the occupation option and refresh the landmarks.

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
        elif response_id == RESPONSE_PREVIEW:
            self._start_preview()
        elif response_id == RESPONSE_GENERATE:
            self._start_generation()
        elif response_id == RESPONSE_STOP:
            self.stop_event.set()
            self._set_status(_("Stopping after the current request..."))
        elif response_id == Gtk.ResponseType.CLOSE:
            # The window manager close button is handled by ManagedWindow
            self.close()

    def _start_preview(self):
        """
        Collect the sources of the first context and show the full request.
        """
        if self.collecting:
            return
        settings = self._run_settings()
        plan = self._plan(settings)
        if not plan.groups:
            self._set_status(_("Nothing to send for these persons."))
            return
        group = plan.groups[0]

        def progress(message: str):
            GLib.idle_add(self._set_status, _("Working: %s") % message)

        def work():
            try:
                pipeline = StoryPipeline(settings, should_stop=self.stop_event.is_set,
                                         progress=progress)
                result, error = pipeline.prepare(group.request), None
            except Exception as exc:  # noqa: BLE001 - reported to the user
                result, error = None, exc
            GLib.idle_add(self._on_preview_done, result, error, group, len(plan.groups))

        self._set_running(True)
        self._set_status(_("Collecting the Wikipedia sources..."))
        set_text(self.result_view, "")
        threading.Thread(target=work, daemon=True).start()

    def _on_preview_done(self, prepared, error: Optional[Exception], group: BatchGroup,
                         group_count: int) -> bool:
        """
        Display the full request, in the GTK main loop.

        Parameters
        ----------
        prepared : PreparedPrompt or None
            Prompts and sources.
        error : Exception or None
            Error raised by the preview.
        group : BatchGroup
            Previewed context.
        group_count : int
            Number of contexts of the batch.

        Returns
        -------
        bool
            False, to run once.
        """
        if not self._is_open():
            return False
        self._set_running(False)
        if isinstance(error, Cancelled):
            self._set_status(_("Stopped."))
            return False
        if error is not None:
            self._set_status(_("Failed."))
            set_text(self.result_view, _("Error: %s") % error)
            return False
        parts = []
        if group_count > 1:
            parts.append(_("Request of the first context (%(name)s), out of %(count)d.")
                         % {"name": group.items[0].facts.name, "count": group_count})
        if prepared.sources.warnings:
            parts.append(_("Warnings:") + "\n" + "\n".join(
                "- %s" % w for w in prepared.sources.warnings))
        richness = _("rich (general knowledge only for daily life)") if prepared.sources.rich \
            else _("limited (marked general knowledge allowed)")
        parts.append(_("Sources: %(count)d excerpt(s), %(richness)s.")
                     % {"count": len(prepared.sources.extracts), "richness": richness})
        parts.append("===== %s =====\n%s" % (_("System prompt"), prepared.system))
        parts.append("===== %s =====\n%s" % (_("User message"), prepared.user))
        set_text(self.result_view, "\n\n".join(parts))
        self._set_status(_("Preview ready: nothing was sent to the LLM."))
        return False

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
        if self.collecting:
            return
        if self.db.readonly:
            ErrorDialog(_("Read-only tree"), _("The family tree is opened read-only."),
                        parent=self.window)
            return
        settings = self._run_settings()
        plan = self._plan(settings)
        if not plan.groups:
            self._show_plan()
            self._set_status(_("Nothing to send for these persons."))
            return
        if len(plan.to_process) > 1 and not self._confirm_batch(plan, settings):
            return
        use_cache = not self.no_cache_check.get_active()
        lang = settings["language"]
        self.batch_report = BatchReport()
        self.batch_total = len(plan.groups)
        self.batch_done = 0
        self.batch_log = []
        single = len(plan.to_process) == 1

        def progress(message: str):
            GLib.idle_add(self._set_status, _("Working: %s") % message)

        def on_start(index: int, group: BatchGroup):
            GLib.idle_add(self._set_status, _("Context %(index)d / %(total)d: %(name)s")
                          % {"index": index + 1, "total": len(plan.groups),
                             "name": group.items[0].facts.name})

        def on_done(group: BatchGroup, result: Optional[GenerationResult],
                    error: Optional[Exception]):
            GLib.idle_add(self._on_group_done, group, result, error, lang, single)

        def work():
            try:
                pipeline = StoryPipeline(settings, should_stop=self.stop_event.is_set,
                                         progress=progress)
                report = run_batch(plan, lambda request: pipeline.generate(request, use_cache),
                                   on_done, self.stop_event.is_set, on_start)
                error = None
            except Exception as exc:  # noqa: BLE001 - reported to the user
                report, error = None, exc
            GLib.idle_add(self._on_batch_done, report, error, single)

        self._set_running(True)
        if not single:
            # The bar shows the processed contexts instead of pulsing
            self._stop_pulse()
        self._set_status(_("Collecting the Wikipedia sources..."))
        set_text(self.result_view, "")
        threading.Thread(target=work, daemon=True).start()

    def _on_group_done(self, group: BatchGroup, result: Optional[GenerationResult],
                       error: Optional[Exception], lang: str, single: bool) -> bool:
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
        single : bool
            Whether only one person is processed (detailed display).

        Returns
        -------
        bool
            False, to run once.
        """
        if not self._is_open():
            return False
        report = self.batch_report
        self.batch_done += 1
        self.progress.set_fraction(self.batch_done / max(self.batch_total, 1))

        if error is not None:
            report.failed += len(group.items)
            for item in group.items:
                self.batch_log.append("✗ %s [%s] — %s" % (item.facts.name, item.facts.gramps_id,
                                                          _("Error: %s") % error))
            self._show_log()
            return False

        validation = result.validation
        warnings = result.sources.warnings + validation.warnings
        content = None
        if validation.ok:
            content = build_note_content(result.text, result.sources.extracts,
                                         result.provider_label, result.model, lang)
        for item in group.items:
            facts = item.facts
            label = "%s [%s]" % (facts.name, facts.gramps_id)
            if content is None:
                report.not_written += 1
                self.batch_log.append("✗ %s — %s" % (label, _("note not written: %s")
                                                     % "; ".join(validation.errors)))
                continue
            try:
                _handle, replaced = write_story_note(self.db, facts.handle, content, facts.name)
            except Exception as exc:  # noqa: BLE001 - reported to the user
                report.failed += 1
                self.batch_log.append("✗ %s — %s" % (
                    label, _("Error while writing the note: %s") % exc))
                continue
            if replaced:
                report.replaced += 1
                status = _("note replaced")
            else:
                report.created += 1
                status = _("note added")
            if warnings:
                report.with_warnings += 1
                status += " " + _("(%d warning(s))") % len(warnings)
            self.batch_log.append("✓ %s — %s" % (label, status))
        if not single:
            self.batch_log.extend("      %s" % w for w in warnings)
        if validation.corrected_problems and not single:
            self.batch_log.append("      " + _("Automatic correction of %d problem(s).")
                                  % len(validation.corrected_problems))

        if single:
            self._show_single_result(group, result, content)
        else:
            self._show_log()
        return False

    def _show_log(self):
        """
        Display the log of the batch, most recent last.
        """
        set_text(self.result_view, "\n".join(self.batch_log))
        buffer = self.result_view.get_buffer()
        self.result_view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0.0, 1.0)

    def _show_single_result(self, group: BatchGroup, result: GenerationResult, content):
        """
        Display the detailed result of a single person.

        Parameters
        ----------
        group : BatchGroup
            Processed group.
        result : GenerationResult
            Generation result.
        content : NoteContent or None
            Written note, None when it was not written.
        """
        report = result.validation
        facts = group.items[0].facts
        lines = list(self.batch_log)
        if not report.ok:
            lines.append(_("Errors:"))
            lines.extend("- %s" % e for e in report.errors)
        if result.from_cache:
            lines.append(_("Text taken from the cache (tick \"Regenerate\" to call the LLM)."))
        if report.corrected_problems:
            lines.append(_("Automatic correction: the text was rewritten to fix these "
                           "problems:"))
            lines.extend("- %s" % p for p in report.corrected_problems)
        warnings = result.sources.warnings + report.warnings
        if warnings:
            lines.append(_("Warnings after correction:") if report.corrected_problems
                         else _("Warnings:"))
            lines.extend("- %s" % w for w in warnings)
        if report.llm_checked and not report.llm_problems:
            lines.append(_("LLM validation: no problem found."))
        lines.append("")
        lines.append(content.text if content is not None else result.text)
        if result.original_text is not None:
            lines.append("")
            lines.append("===== %s =====" % _("Text before the automatic correction"))
            lines.append(result.original_text)
        set_text(self.result_view, "\n".join(lines))
        if content is None:
            self._set_status(_("The note was not written."))
        elif self.batch_report.replaced:
            self._set_status(_("The note of %s was replaced.") % facts.name)
        else:
            self._set_status(_("A note was added to %s.") % facts.name)

    def _on_batch_done(self, run_report: Optional[BatchReport], error: Optional[Exception],
                       single: bool) -> bool:
        """
        Show the final report of a run, in the GTK main loop.

        Parameters
        ----------
        run_report : BatchReport or None
            Cancellation and abort information.
        error : Exception or None
            Error that stopped the run before the first group.
        single : bool
            Whether only one person was processed.

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
            set_text(self.result_view, _("Error: %s") % error)
            return False
        report.cancelled = run_report.cancelled
        report.aborted = run_report.aborted
        if single and not (report.cancelled or report.aborted or report.failed):
            # The detailed result is already displayed
            self._refresh_notes()
            return False

        summary = [_("Notes added: %(created)d — replaced: %(replaced)d — not written: "
                     "%(not_written)d — errors: %(failed)d.")
                   % {"created": report.created, "replaced": report.replaced,
                      "not_written": report.not_written, "failed": report.failed}]
        if report.with_warnings:
            summary.append(_("%d note(s) written with warnings: review them.")
                           % report.with_warnings)
        if report.aborted:
            summary.append(_("Stopped after %(count)d failures in a row: %(error)s")
                           % {"count": MAX_CONSECUTIVE_ERRORS, "error": report.aborted})
            self._set_status(_("Stopped after repeated errors."))
        elif report.cancelled:
            summary.append(_("Stopped by the user: the remaining persons were not processed."))
            self._set_status(_("Stopped."))
        else:
            self._set_status(_("Done."))
        self.batch_log.append("")
        self.batch_log.extend(summary)
        self._show_log()
        self._refresh_notes()
        return False

    def _refresh_notes(self):
        """
        Update the persons who now have a note.
        """
        handles = [facts.handle for facts in self.facts_list]
        self.existing_notes = persons_with_story_note(self.db, handles)

    def close(self, *obj):
        """
        Stop the running request and close the window.
        """
        self.stop_event.set()
        self.refresh_serial += 1
        self._stop_pulse()
        if self.refresh_timer is not None:
            GLib.source_remove(self.refresh_timer)
            self.refresh_timer = None
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
