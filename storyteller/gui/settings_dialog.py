"""
Settings dialog: LLM provider, API key, text options and prompts.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import threading
from typing import Dict, Optional

# Dependencies #
from gi.repository import GLib, Gtk

# Local imports #
from .. import credentials, prompts
from ..cache import DiskCache
from ..config import Settings, cache_dir
from ..i18n import _
from ..llm.base import LLMError
from ..llm.registry import PRESETS, create_provider
from .widgets import framed, get_text, set_text, text_view, wrapped_label

#############
# Constants #
#############

LANGUAGES = (("fr", "Français"), ("en", "English"))

###########
# Classes #
###########


class SettingsDialog:
    """
    Modal settings dialog.

    Parameters
    ----------
    parent : Gtk.Window
        Parent window.
    settings : Settings
        Settings to edit; saved when the user validates.
    """

    def __init__(self, parent: Gtk.Window, settings: Settings):
        self.settings = settings
        self.provider_id = settings["provider"]
        self.language = settings["language"]
        # Edited but not yet saved provider fields: id -> {"base_url", "model"}
        self.provider_fields: Dict[str, Dict[str, str]] = {}
        # Typed API keys: id -> key ("" means delete)
        self.typed_keys: Dict[str, str] = {}

        self.dialog = Gtk.Dialog(title=_("Storyteller settings"), transient_for=parent,
                                 modal=True)
        self.dialog.set_default_size(720, 620)
        self.dialog.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.dialog.add_button(_("_Save"), Gtk.ResponseType.OK)
        self.dialog.set_default_response(Gtk.ResponseType.OK)

        notebook = Gtk.Notebook()
        notebook.append_page(self._build_model_page(), Gtk.Label(label=_("Model")))
        notebook.append_page(self._build_text_page(), Gtk.Label(label=_("Text")))
        notebook.append_page(self._build_prompts_page(), Gtk.Label(label=_("Prompts")))
        area = self.dialog.get_content_area()
        area.set_spacing(6)
        area.pack_start(notebook, True, True, 0)

        self._load_provider(self.provider_id)

    # Pages

    def _build_model_page(self) -> Gtk.Widget:
        """
        Build the provider page.

        Returns
        -------
        Gtk.Widget
            Page.
        """
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        row = 0

        self.provider_combo = Gtk.ComboBoxText()
        for provider_id, preset in PRESETS.items():
            self.provider_combo.append(provider_id, preset.label)
        self.provider_combo.set_active_id(self.provider_id)
        self.provider_combo.connect("changed", self._on_provider_changed)
        grid.attach(Gtk.Label(label=_("Provider:"), xalign=0), 0, row, 1, 1)
        grid.attach(self.provider_combo, 1, row, 2, 1)
        row += 1

        self.base_url_entry = Gtk.Entry(hexpand=True)
        grid.attach(Gtk.Label(label=_("API URL:"), xalign=0), 0, row, 1, 1)
        grid.attach(self.base_url_entry, 1, row, 2, 1)
        row += 1

        self.model_combo = Gtk.ComboBoxText.new_with_entry()
        self.model_combo.set_hexpand(True)
        list_button = Gtk.Button(label=_("List models"))
        list_button.connect("clicked", self._on_list_models)
        grid.attach(Gtk.Label(label=_("Model:"), xalign=0), 0, row, 1, 1)
        grid.attach(self.model_combo, 1, row, 1, 1)
        grid.attach(list_button, 2, row, 1, 1)
        row += 1

        self.key_entry = Gtk.Entry(hexpand=True)
        self.key_entry.set_visibility(False)
        self.key_entry.connect("changed", self._on_key_changed)
        delete_key = Gtk.Button(label=_("Delete key"))
        delete_key.connect("clicked", self._on_delete_key)
        grid.attach(Gtk.Label(label=_("API key:"), xalign=0), 0, row, 1, 1)
        grid.attach(self.key_entry, 1, row, 1, 1)
        grid.attach(delete_key, 2, row, 1, 1)
        row += 1

        show_key = Gtk.CheckButton(label=_("Show the key"))
        show_key.connect("toggled", lambda b: self.key_entry.set_visibility(b.get_active()))
        grid.attach(show_key, 1, row, 2, 1)
        row += 1

        self.key_status = wrapped_label()
        grid.attach(self.key_status, 1, row, 2, 1)
        row += 1

        self.send_temperature = Gtk.CheckButton(label=_("Send a temperature:"))
        self.send_temperature.set_active(bool(self.settings["send_temperature"]))
        self.temperature = Gtk.SpinButton.new_with_range(0.0, 2.0, 0.1)
        self.temperature.set_digits(1)
        self.temperature.set_value(float(self.settings["temperature"]))
        grid.attach(self.send_temperature, 0, row, 1, 1)
        grid.attach(self.temperature, 1, row, 1, 1)
        row += 1
        grid.attach(wrapped_label(_("Recent Claude models and some reasoning models do not "
                                    "accept a temperature: it is then ignored.")), 1, row, 2, 1)
        row += 1

        self.max_tokens = Gtk.SpinButton.new_with_range(256, 64000, 256)
        self.max_tokens.set_value(int(self.settings["max_tokens"]))
        grid.attach(Gtk.Label(label=_("Max tokens:"), xalign=0), 0, row, 1, 1)
        grid.attach(self.max_tokens, 1, row, 1, 1)
        row += 1

        test_button = Gtk.Button(label=_("Test the connection"))
        test_button.connect("clicked", self._on_test)
        self.test_status = wrapped_label(selectable=True)
        grid.attach(test_button, 0, row, 1, 1)
        grid.attach(self.test_status, 1, row, 2, 1)
        return grid

    def _build_text_page(self) -> Gtk.Widget:
        """
        Build the text options page.

        Returns
        -------
        Gtk.Widget
            Page.
        """
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        row = 0

        self.language_combo = Gtk.ComboBoxText()
        for code, label in LANGUAGES:
            self.language_combo.append(code, label)
        self.language_combo.set_active_id(self.language)
        self.language_combo.connect("changed", self._on_language_changed)
        grid.attach(Gtk.Label(label=_("Language of the text and of Wikipedia:"), xalign=0),
                    0, row, 1, 1)
        grid.attach(self.language_combo, 1, row, 1, 1)
        row += 1

        self.length = Gtk.SpinButton.new_with_range(100, 2000, 50)
        self.length.set_value(int(self.settings["length_words"]))
        grid.attach(Gtk.Label(label=_("Target length (words):"), xalign=0), 0, row, 1, 1)
        grid.attach(self.length, 1, row, 1, 1)
        row += 1

        self.include_occupation = Gtk.CheckButton(
            label=_("Send the occupation (as entered in the tree)"))
        self.include_occupation.set_active(bool(self.settings["include_occupation"]))
        grid.attach(self.include_occupation, 0, row, 2, 1)
        row += 1

        self.llm_validation = Gtk.CheckButton(
            label=_("Check the text with a second LLM request (costs more tokens)"))
        self.llm_validation.set_active(bool(self.settings["llm_validation"]))
        grid.attach(self.llm_validation, 0, row, 2, 1)
        row += 1

        self.auto_correction = Gtk.CheckButton(
            label=_("Rewrite the text when the validation finds problems, then check it again "
                    "(two more requests)"))
        self.auto_correction.set_active(bool(self.settings["auto_correction"]))
        self.auto_correction.set_margin_start(24)
        self.auto_correction.set_sensitive(self.llm_validation.get_active())
        self.llm_validation.connect(
            "toggled", lambda b: self.auto_correction.set_sensitive(b.get_active()))
        grid.attach(self.auto_correction, 0, row, 2, 1)
        row += 1

        clear_cache = Gtk.Button(label=_("Empty the cache (Wikipedia and texts)"))
        clear_cache.connect("clicked", self._on_clear_cache)
        self.cache_status = wrapped_label()
        grid.attach(clear_cache, 0, row, 1, 1)
        grid.attach(self.cache_status, 1, row, 1, 1)
        return grid

    def _build_prompts_page(self) -> Gtk.Widget:
        """
        Build the prompts page.

        Returns
        -------
        Gtk.Widget
            Page.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        box.pack_start(wrapped_label(_(
            "Available variables: {reperes} (landmarks), {profession}, {sources}, "
            "{consigne_sources} (instruction depending on the richness of the sources), "
            "{langue}, {longueur} and, for the check and the correction, {texte} and "
            "{problemes}.")), False, False, 0)
        self.prompt_views = {}
        titles = {
            "system": _("System prompt"),
            "user": _("User message"),
            "validation": _("Check prompt (optional LLM validation)"),
            "correction": _("Correction prompt (optional automatic correction)"),
        }
        for kind in prompts.KINDS:
            scrolled, view = text_view(editable=True, height=110)
            set_text(view, prompts.template_or_default(self.settings["%s_prompt" % kind], kind,
                                                       self.language))
            reset = Gtk.Button(label=_("Reset to default"))
            reset.connect("clicked", self._on_reset_prompt, kind)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.pack_start(scrolled, True, True, 0)
            inner.pack_start(reset, False, False, 0)
            reset.set_halign(Gtk.Align.END)
            box.pack_start(framed(titles[kind], inner), True, True, 0)
            self.prompt_views[kind] = view
        scrolled_page = Gtk.ScrolledWindow()
        scrolled_page.add(box)
        return scrolled_page

    # Provider fields

    def _store_provider_fields(self):
        """
        Remember the fields of the displayed provider.
        """
        self.provider_fields[self.provider_id] = {
            "base_url": self.base_url_entry.get_text().strip(),
            "model": self.model_combo.get_child().get_text().strip(),
        }

    def _load_provider(self, provider_id: str):
        """
        Display the fields of a provider.

        Parameters
        ----------
        provider_id : str
            Provider identifier.
        """
        fields = self.provider_fields.get(provider_id, {})
        self.base_url_entry.set_text(
            fields.get("base_url") or self.settings.provider_setting(provider_id, "base_url"))
        self.model_combo.remove_all()
        self.model_combo.get_child().set_text(
            fields.get("model") or self.settings.provider_setting(provider_id, "model"))
        self.key_entry.handler_block_by_func(self._on_key_changed)
        self.key_entry.set_text(self.typed_keys.get(provider_id, ""))
        self.key_entry.handler_unblock_by_func(self._on_key_changed)
        self._update_key_status()
        self.test_status.set_text("")

    def _update_key_status(self):
        """
        Describe where the key of the displayed provider is stored.
        """
        preset = PRESETS[self.provider_id]
        typed = self.typed_keys.get(self.provider_id)
        if typed == "":
            text = _("The stored key will be deleted.")
        elif typed:
            text = _("The new key will be saved.")
        else:
            source = credentials.key_source(self.provider_id)
            variable = credentials.env_var_name(self.provider_id)
            if source == "env":
                text = _("Key read from the environment variable %s (it takes precedence).") % (
                    variable or credentials.GENERIC_ENV_VAR)
            elif source == "keyring":
                text = _("A key is stored in the system keyring.")
            elif source == "file":
                text = _("A key is stored in %s (readable by you only).") % (
                    credentials.credentials_file())
            elif preset.needs_key:
                text = _("No key configured.")
            else:
                text = _("No key needed for a local server.")
        self.key_status.set_text(text)

    def _on_provider_changed(self, combo: Gtk.ComboBoxText):
        """
        Switch the displayed provider.

        Parameters
        ----------
        combo : Gtk.ComboBoxText
            Provider combo.
        """
        new_id = combo.get_active_id()
        if not new_id or new_id == self.provider_id:
            return
        self._store_provider_fields()
        self.provider_id = new_id
        self._load_provider(new_id)

    def _on_key_changed(self, entry: Gtk.Entry):
        """
        Remember a typed key.

        Parameters
        ----------
        entry : Gtk.Entry
            Key entry.
        """
        text = entry.get_text().strip()
        if text:
            self.typed_keys[self.provider_id] = text
        else:
            self.typed_keys.pop(self.provider_id, None)
        self._update_key_status()

    def _on_delete_key(self, _button):
        """
        Mark the stored key of the displayed provider for deletion.
        """
        self.key_entry.handler_block_by_func(self._on_key_changed)
        self.key_entry.set_text("")
        self.key_entry.handler_unblock_by_func(self._on_key_changed)
        self.typed_keys[self.provider_id] = ""
        self._update_key_status()

    # Network actions

    def _current_provider(self):
        """
        Build a provider from the displayed (unsaved) fields.

        Returns
        -------
        LLMProvider
            Provider.
        """
        key = self.typed_keys.get(self.provider_id)
        if key is None:
            key = credentials.get_api_key(self.provider_id)
        temperature = self.temperature.get_value() if self.send_temperature.get_active() else None
        return create_provider(self.provider_id, self.base_url_entry.get_text().strip(),
                               self.model_combo.get_child().get_text().strip(), key or None,
                               temperature, int(self.max_tokens.get_value()))

    def _run_in_thread(self, work, done):
        """
        Run a blocking call in a thread and deliver its result in the GTK
        main loop.

        Parameters
        ----------
        work : callable
            Blocking function without argument.
        done : callable
            Called with (result, error) in the main loop.
        """
        def target():
            try:
                result, error = work(), None
            except Exception as exc:  # noqa: BLE001 - reported to the user
                result, error = None, exc
            GLib.idle_add(done, result, error)

        threading.Thread(target=target, daemon=True).start()

    def _on_list_models(self, _button):
        """
        Fill the model list from the API.
        """
        provider = self._current_provider()
        self.test_status.set_text(_("Listing the models..."))
        self._run_in_thread(provider.list_models, self._on_models_listed)

    def _on_models_listed(self, models, error) -> bool:
        """
        Display the listed models.

        Parameters
        ----------
        models : list of str or None
            Models.
        error : Exception or None
            Error.

        Returns
        -------
        bool
            False, to run once.
        """
        if error is not None:
            self.test_status.set_text(_("Error: %s") % error)
            return False
        current = self.model_combo.get_child().get_text()
        self.model_combo.remove_all()
        for model in models:
            self.model_combo.append_text(model)
        self.model_combo.get_child().set_text(current)
        self.test_status.set_text(_("%d models available: open the list to choose one.")
                                  % len(models))
        return False

    def _on_test(self, _button):
        """
        Check the connection by sending a tiny request.
        """
        provider = self._current_provider()
        provider.max_tokens = max(provider.max_tokens, 1024)
        self.test_status.set_text(_("Testing..."))
        self._run_in_thread(lambda: provider.generate("Answer with one word.", "Say OK."),
                            self._on_tested)

    def _on_tested(self, response, error) -> bool:
        """
        Display the result of the connection test.

        Parameters
        ----------
        response : LLMResponse or None
            Answer.
        error : Exception or None
            Error.

        Returns
        -------
        bool
            False, to run once.
        """
        if error is not None:
            message = str(error) if isinstance(error, LLMError) else repr(error)
            self.test_status.set_text(_("Connection failed: %s") % message)
        else:
            self.test_status.set_text(_("Connection OK (model %(model)s answered: %(text)s)")
                                      % {"model": response.model, "text": response.text[:40]})
        return False

    # Other actions

    def _on_clear_cache(self, _button):
        """
        Empty the cache.
        """
        count = DiskCache(cache_dir()).clear()
        self.cache_status.set_text(_("%d cached entries deleted.") % count)

    def _on_reset_prompt(self, _button, kind: str):
        """
        Restore the default prompt of a kind.

        Parameters
        ----------
        kind : str
            "system", "user", "validation" or "correction".
        """
        set_text(self.prompt_views[kind], prompts.default_prompt(kind, self.language))

    def _on_language_changed(self, combo: Gtk.ComboBoxText):
        """
        Switch the default prompts to the new language when they were not
        customized.

        Parameters
        ----------
        combo : Gtk.ComboBoxText
            Language combo.
        """
        new_language = combo.get_active_id()
        for kind, view in self.prompt_views.items():
            if get_text(view).strip() == prompts.default_prompt(kind, self.language).strip():
                set_text(view, prompts.default_prompt(kind, new_language))
        self.language = new_language

    # Saving

    def _save(self):
        """
        Write the edited values to the settings and the key storage.
        """
        self._store_provider_fields()
        for provider_id, fields in self.provider_fields.items():
            for key, value in fields.items():
                self.settings.set_provider_setting(provider_id, key, value)
        for provider_id, key in self.typed_keys.items():
            credentials.set_api_key(provider_id, key)

        self.settings["provider"] = self.provider_id
        self.settings["send_temperature"] = self.send_temperature.get_active()
        self.settings["temperature"] = round(self.temperature.get_value(), 2)
        self.settings["max_tokens"] = int(self.max_tokens.get_value())
        self.settings["language"] = self.language
        self.settings["length_words"] = int(self.length.get_value())
        self.settings["include_occupation"] = self.include_occupation.get_active()
        self.settings["llm_validation"] = self.llm_validation.get_active()
        self.settings["auto_correction"] = self.auto_correction.get_active()
        for kind, view in self.prompt_views.items():
            text = get_text(view)
            is_default = text.strip() == prompts.default_prompt(kind, self.language).strip()
            self.settings["%s_prompt" % kind] = None if is_default or not text.strip() else text
        self.settings.save()

    def run(self) -> bool:
        """
        Show the dialog until the user closes it.

        Returns
        -------
        bool
            True when the settings were saved.
        """
        self.dialog.show_all()
        saved = False
        if self.dialog.run() == Gtk.ResponseType.OK:
            self._save()
            saved = True
        self.dialog.destroy()
        return saved


#############
# Functions #
#############


def edit_settings(parent: Optional[Gtk.Window], settings: Settings) -> bool:
    """
    Open the settings dialog.

    Parameters
    ----------
    parent : Gtk.Window or None
        Parent window.
    settings : Settings
        Settings to edit.

    Returns
    -------
    bool
        True when the settings were saved.
    """
    return SettingsDialog(parent, settings).run()
