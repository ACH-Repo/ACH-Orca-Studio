"""Hover-tooltips for Tkinter widgets.

Tkinter has no built-in tooltip. This is the standard pattern: bind <Enter>
and <Leave>, schedule a delayed Toplevel popup with a yellow label, place it
just below the widget. The Toplevel uses wm_overrideredirect(True) so it has
no window decorations and doesn't steal focus.

Usage:
    from orca_workbench.ui.tooltip import Tooltip
    Tooltip(my_button, "What this button does.")

The returned Tooltip object is kept alive by the widget's binding table — you
don't need to keep a reference yourself.
"""

import tkinter as tk


_BG = "#ffffe0"
_FG = "#000000"
_BORDER = "#888888"

# Global on/off switch for every tooltip in the app. Toggled from the toolbar
# checkbox so experienced users can silence the hints. Checked at show time.
_ENABLED = True


def set_enabled(value):
    # type: (bool) -> None
    global _ENABLED
    _ENABLED = bool(value)


def is_enabled():
    # type: () -> bool
    return _ENABLED


class Tooltip(object):
    def __init__(self, widget, text, delay=450, wraplength=320):
        # type: (tk.Widget, str, int, int) -> None
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
        if not _ENABLED:
            return
        self._unschedule()
        self._after_id = self.widget.after(self.delay, self._show)

    def _unschedule(self):
        aid = self._after_id
        self._after_id = None
        if aid is not None:
            try:
                self.widget.after_cancel(aid)
            except tk.TclError:
                pass

    def _show(self):
        if self._tip is not None or not _ENABLED:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry("+{}+{}".format(x, y))
        label = tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            background=_BG,
            foreground=_FG,
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=0,
            wraplength=self.wraplength,
            padx=6,
            pady=4,
            font=("TkDefaultFont", 9),
        )
        label.pack()
        # Keep above other windows if the WM supports it.
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._tip = tip

    def _hide(self, _event=None):
        self._unschedule()
        tip = self._tip
        self._tip = None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass


def tip(widget, text):
    # type: (tk.Widget, str) -> Tooltip
    """Convenience wrapper — same as `Tooltip(widget, text)` but returns the object."""
    return Tooltip(widget, text)
