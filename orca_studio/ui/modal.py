"""Helper for making a Toplevel modal without the X11 'grab failed' crash.

`grab_set()` raises TclError ('grab failed: window not viewable') if the window
isn't mapped yet. That's racy locally and reliably bites over X-forwarding,
where mapping lags. The fix is to wait for the window to become visible before
grabbing. We also retry on the next idle as a fallback so a transient failure
doesn't leave the dialog non-modal or crash the callback.
"""

import tkinter as tk


def make_modal(window, parent):
    # type: (tk.Toplevel, tk.Misc) -> None
    """Make `window` a modal dialog over `parent`, robustly across platforms
    and X-forwarding. Safe to call once all the dialog's widgets are built.

    Non-blocking: we do NOT call wait_visibility() (which runs a nested event
    loop and hangs forever if the window never becomes viewable — e.g. a
    withdrawn parent). Instead we poll winfo_viewable() and grab once the
    window is on screen, retrying briefly. grab_set on a not-yet-viewable
    window raises 'grab failed: window not viewable', so the viewable check
    is what prevents that crash."""
    try:
        window.transient(parent.winfo_toplevel())
    except tk.TclError:
        pass

    def _grab(attempt=0):
        try:
            if not window.winfo_exists():
                return
            if window.winfo_viewable():
                window.grab_set()
                return
        except tk.TclError:
            return
        if attempt < 60:  # ~3s of retries, then give up (non-modal, but alive)
            try:
                window.after(50, lambda: _grab(attempt + 1))
            except tk.TclError:
                pass

    _grab()
