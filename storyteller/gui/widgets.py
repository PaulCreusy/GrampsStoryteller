"""
Small GTK helpers shared by the windows.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from typing import Tuple

# Dependencies #
from gi.repository import Gtk, Pango

#############
# Functions #
#############


def text_view(editable: bool = False, monospace: bool = False,
              height: int = 120) -> Tuple[Gtk.ScrolledWindow, Gtk.TextView]:
    """
    Create a wrapped text view inside a scrolled window.

    Parameters
    ----------
    editable : bool
        Whether the user can edit the text.
    monospace : bool
        Whether to use a fixed-width font.
    height : int
        Minimum height in pixels.

    Returns
    -------
    tuple of (Gtk.ScrolledWindow, Gtk.TextView)
        Container and view.
    """
    view = Gtk.TextView()
    view.set_editable(editable)
    view.set_cursor_visible(editable)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_monospace(monospace)
    view.set_left_margin(6)
    view.set_right_margin(6)
    scrolled = Gtk.ScrolledWindow()
    # The text wraps: a horizontal scrollbar would let the window grow to
    # the height of the whole text
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.set_min_content_height(height)
    scrolled.add(view)
    return scrolled, view


def set_text(view: Gtk.TextView, text: str):
    """
    Replace the text of a view.

    Parameters
    ----------
    view : Gtk.TextView
        View.
    text : str
        New text.
    """
    view.get_buffer().set_text(text or "")


def get_text(view: Gtk.TextView) -> str:
    """
    Return the whole text of a view.

    Parameters
    ----------
    view : Gtk.TextView
        View.

    Returns
    -------
    str
        Text.
    """
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


def framed(title: str, child: Gtk.Widget) -> Gtk.Frame:
    """
    Put a widget in a titled frame.

    Parameters
    ----------
    title : str
        Frame title.
    child : Gtk.Widget
        Content.

    Returns
    -------
    Gtk.Frame
        Frame.
    """
    frame = Gtk.Frame(label=title)
    child.set_margin_start(6)
    child.set_margin_end(6)
    child.set_margin_top(4)
    child.set_margin_bottom(6)
    frame.add(child)
    return frame


def wrapped_label(text: str = "", selectable: bool = False) -> Gtk.Label:
    """
    Create a left-aligned wrapping label.

    Parameters
    ----------
    text : str
        Label text.
    selectable : bool
        Whether the text can be selected.

    Returns
    -------
    Gtk.Label
        Label.
    """
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_selectable(selectable)
    return label
