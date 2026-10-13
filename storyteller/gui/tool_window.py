"""
Main window of the tool: shows what will be sent for the active person,
previews the full request and generates the note.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import copy
import threading
from typing import Optional

# Dependencies #
from gi.repository import GLib, Gtk
from gramps.gen.display.name import displayer as name_displayer
from gramps.gui.dialog import ErrorDialog
from gramps.gui.managedwindow import ManagedWindow
from gramps.gui.plug import tool

# Local imports #
from ..anonymizer import build_request
from ..config import Settings
from ..extraction import SKIP_LIVING, SKIP_NO_LANDMARK, SKIP_PRIVATE, extract_person
from ..http import Cancelled
from ..i18n import _
from ..llm.registry import PRESETS
from ..models import ContextRequest, GenerationResult, PersonFacts
from ..note_content import build_note_content
from ..notes import write_story_note
from ..pipeline import StoryPipeline
from .settings_dialog import edit_settings
from .widgets import framed, set_text, text_view, wrapped_label

#############
# Constants #
#############

RESPONSE_SETTINGS = 1
RESPONSE_PREVIEW = 2
RESPONSE_GENERATE = 3
RESPONSE_STOP = 4

###########
# Classes #
###########


class StorytellerTool(tool.Tool, ManagedWindow):
    """
    Gramps tool generating the sourced historical context of a person.

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
        self.facts: Optional[PersonFacts] = None
        self.stop_event = threading.Event()
        self.running = False
        self.pulse_id = None

        self._build()
        self.set_window(self.dialog, None, self.label)
        self.setup_configs("interface.grampsstoryteller", 720, 640)
        self.show()
        self._use_active_person()

    # Construction

    def _build(self):
        """
        Create the widgets.
        """
        self.dialog = Gtk.Dialog()
        self.settings_button = self.dialog.add_button(_("_Settings..."), RESPONSE_SETTINGS)
        self.preview_button = self.dialog.add_button(_("_Preview the request"), RESPONSE_PREVIEW)
        self.stop_button = self.dialog.add_button(_("_Stop"), RESPONSE_STOP)
        self.generate_button = self.dialog.add_button(_("_Generate the note"), RESPONSE_GENERATE)
        self.dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", self._on_response)

        box = self.dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(8)

        person_row = Gtk.Box(spacing=8)
        self.person_label = wrapped_label()
        self.person_label.set_hexpand(True)
        refresh = Gtk.Button(label=_("Use the active person"))
        refresh.connect("clicked", lambda _b: self._use_active_person())
        person_row.pack_start(self.person_label, True, True, 0)
        person_row.pack_start(refresh, False, False, 0)
        box.pack_start(person_row, False, False, 0)

        sent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled, self.sent_view = text_view(monospace=True, height=80)
        sent_box.pack_start(wrapped_label(_(
            "Only these lines (and the matching Wikipedia excerpts) are sent. No name, no "
            "identifier and no event type ever leave Gramps.")), False, False, 0)
        sent_box.pack_start(scrolled, True, True, 0)
        box.pack_start(framed(_("Anonymized landmarks"), sent_box), False, True, 0)

        options = Gtk.Box(spacing=12)
        self.occupation_check = Gtk.CheckButton(label=_("Send the occupation"))
        self.occupation_check.set_active(bool(self.settings["include_occupation"]))
        self.occupation_check.connect("toggled", self._on_occupation_toggled)
        self.validation_check = Gtk.CheckButton(label=_("LLM validation"))
        self.validation_check.set_active(bool(self.settings["llm_validation"]))
        self.validation_check.connect("toggled", self._on_validation_toggled)
        self.validation_check.set_tooltip_text(
            _("Checks the text with a second request (costs more tokens)."))
        self.no_cache_check = Gtk.CheckButton(label=_("Regenerate (ignore the cache)"))
        for widget in (self.occupation_check, self.validation_check, self.no_cache_check):
            options.pack_start(widget, False, False, 0)
        box.pack_start(options, False, False, 0)

        self.model_label = wrapped_label()
        box.pack_start(self.model_label, False, False, 0)
        self._update_model_label()

        self.progress = Gtk.ProgressBar()
        self.status_label = wrapped_label()
        box.pack_start(self.progress, False, False, 0)
        box.pack_start(self.status_label, False, False, 0)

        scrolled, self.result_view = text_view(height=220)
        box.pack_start(framed(_("Result"), scrolled), True, True, 0)
        self.dialog.show_all()
        self._set_running(False)

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

    def _set_running(self, running: bool):
        """
        Enable or disable the actions while a request runs.

        Parameters
        ----------
        running : bool
            Whether a request is running.
        """
        self.running = running
        for button in (self.settings_button, self.preview_button, self.generate_button):
            button.set_sensitive(not running)
        self.stop_button.set_sensitive(running)
        if running:
            self.stop_event.clear()
            self.progress.set_fraction(0.0)
            if self.pulse_id is None:
                self.pulse_id = GLib.timeout_add(120, self._pulse)
        else:
            if self.pulse_id is not None:
                GLib.source_remove(self.pulse_id)
                self.pulse_id = None
            self.progress.set_fraction(0.0)

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
        self.status_label.set_text(text)
        return False

    # Person

    def _use_active_person(self):
        """
        Load the active person and show the anonymized landmarks.
        """
        handle = self.uistate.get_active("Person")
        person = self.db.get_person_from_handle(handle) if handle else None
        if person is None:
            self.facts = None
            self.person_label.set_markup("<b>%s</b>" % GLib.markup_escape_text(
                _("No active person: select a person in the People view.")))
            set_text(self.sent_view, "")
            return
        name = name_displayer.display(person)
        self.facts = extract_person(self.db, person, name, include_occupation=True)
        self.person_label.set_markup("<b>%s</b> [%s]" % (
            GLib.markup_escape_text(name), GLib.markup_escape_text(person.get_gramps_id())))
        self._show_request()

    def _request(self) -> Optional[ContextRequest]:
        """
        Build the anonymized request of the current person.

        Returns
        -------
        ContextRequest or None
            Request, None when the person cannot be processed.
        """
        if self.facts is None or self.facts.skip_reason:
            return None
        return build_request(self.facts, self.settings["language"],
                             self.occupation_check.get_active())

    def _show_request(self):
        """
        Display the landmarks that will be sent, or why nothing will be sent.
        """
        reasons = {
            SKIP_LIVING: _("This person is probably alive: living persons are always excluded."),
            SKIP_PRIVATE: _("This person is marked private: nothing will be sent."),
            SKIP_NO_LANDMARK: _("No usable birth, baptism, death or burial year or place."),
        }
        if self.facts is None:
            set_text(self.sent_view, "")
            return
        if self.facts.skip_reason:
            set_text(self.sent_view, reasons.get(self.facts.skip_reason, self.facts.skip_reason))
            return
        request = self._request()
        lines = list(request.lines)
        if request.occupation:
            lines.append(_("Occupation: %s") % request.occupation)
        set_text(self.sent_view, "\n".join(lines))

    def _on_occupation_toggled(self, button: Gtk.CheckButton):
        """
        Remember the occupation option and refresh the landmarks.

        Parameters
        ----------
        button : Gtk.CheckButton
            Occupation check box.
        """
        self._remember("include_occupation", button.get_active())
        self._show_request()

    def _on_validation_toggled(self, button: Gtk.CheckButton):
        """
        Remember the LLM validation option.

        Parameters
        ----------
        button : Gtk.CheckButton
            Validation check box.
        """
        self._remember("llm_validation", button.get_active())

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
                self._update_model_label()
                self._show_request()
        elif response_id == RESPONSE_PREVIEW:
            self._start(preview=True)
        elif response_id == RESPONSE_GENERATE:
            self._start(preview=False)
        elif response_id == RESPONSE_STOP:
            self.stop_event.set()
            self._set_status(_("Stopping..."))
        elif response_id == Gtk.ResponseType.CLOSE:
            # The window manager close button is handled by ManagedWindow
            self.close()

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
        return settings

    def _start(self, preview: bool):
        """
        Start a preview or a generation in a worker thread.

        Parameters
        ----------
        preview : bool
            True to only collect the sources and show the prompt.
        """
        request = self._request()
        if request is None:
            self._show_request()
            self._set_status(_("Nothing to send for this person."))
            return
        if not preview and self.db.readonly:
            ErrorDialog(_("Read-only tree"), _("The family tree is opened read-only."),
                        parent=self.window)
            return
        settings = self._run_settings()
        use_cache = not self.no_cache_check.get_active()
        person_handle, person_name = self.facts.handle, self.facts.name

        def progress(message: str):
            GLib.idle_add(self._set_status, _("Working: %s") % message)

        def work():
            try:
                pipeline = StoryPipeline(settings, should_stop=self.stop_event.is_set,
                                         progress=progress)
                if preview:
                    result = pipeline.prepare(request)
                else:
                    result = pipeline.generate(request, use_cache=use_cache)
                error = None
            except Exception as exc:  # noqa: BLE001 - reported to the user
                result, error = None, exc
            GLib.idle_add(self._on_done, preview, result, error, person_handle, person_name,
                          settings["language"])

        self._set_running(True)
        self._set_status(_("Collecting the Wikipedia sources..."))
        set_text(self.result_view, "")
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, preview: bool, result, error: Optional[Exception], person_handle: str,
                 person_name: str, lang: str) -> bool:
        """
        Handle the end of a worker run, in the GTK main loop.

        Parameters
        ----------
        preview : bool
            Whether the run was a preview.
        result : PreparedPrompt or GenerationResult or None
            Run result.
        error : Exception or None
            Error raised by the run.
        person_handle : str
            Handle of the processed person.
        person_name : str
            Name of the processed person.
        lang : str
            Language of the text.

        Returns
        -------
        bool
            False, to run once.
        """
        if not getattr(self, "opened", False) or self.window is None:
            return False
        self._set_running(False)
        if isinstance(error, Cancelled):
            self._set_status(_("Stopped."))
            return False
        if error is not None:
            self._set_status(_("Failed."))
            set_text(self.result_view, _("Error: %s") % error)
            return False
        if preview:
            self._show_preview(result)
        else:
            self._write_note(result, person_handle, person_name, lang)
        return False

    def _show_preview(self, prepared):
        """
        Display the full request.

        Parameters
        ----------
        prepared : PreparedPrompt
            Prompts and sources.
        """
        parts = []
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

    def _write_note(self, result: GenerationResult, person_handle: str, person_name: str,
                    lang: str):
        """
        Write the note and display the report.

        Parameters
        ----------
        result : GenerationResult
            Generation result.
        person_handle : str
            Person handle.
        person_name : str
            Person name.
        lang : str
            Language of the text.
        """
        report = result.validation
        lines = []
        display_text = result.text
        if not report.ok:
            self._set_status(_("The note was not written."))
            lines.append(_("Errors:"))
            lines.extend("- %s" % e for e in report.errors)
        else:
            content = build_note_content(result.text, result.sources.extracts,
                                         result.provider_label, result.model, lang)
            try:
                _handle, replaced = write_story_note(self.db, person_handle, content, person_name)
            except Exception as exc:  # noqa: BLE001 - reported to the user
                self._set_status(_("Failed."))
                set_text(self.result_view, _("Error while writing the note: %s") % exc)
                return
            display_text = content.text
            if replaced:
                self._set_status(_("The note of %s was replaced.") % person_name)
            else:
                self._set_status(_("A note was added to %s.") % person_name)
        if result.from_cache:
            lines.append(_("Text taken from the cache (tick \"Regenerate\" to call the LLM)."))
        warnings = result.sources.warnings + report.warnings
        if warnings:
            lines.append(_("Warnings:"))
            lines.extend("- %s" % w for w in warnings)
        if report.llm_checked and not report.llm_problems:
            lines.append(_("LLM validation: no problem found."))
        lines.append("")
        lines.append(display_text)
        set_text(self.result_view, "\n".join(lines))

    def close(self, *obj):
        """
        Stop the running request and close the window.
        """
        self.stop_event.set()
        if self.pulse_id is not None:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = None
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
