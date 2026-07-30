"""Helper for making a Toplevel modal without the X11 'grab failed' crash.

`grab_set()` raises TclError ('grab failed: window not viewable') if the window
isn't mapped yet. That's racy locally and reliably bites over X-forwarding,
where mapping lags. The fix is to wait for the window to become visible before
grabbing. We also retry on the next idle as a fallback so a transient failure
doesn't leave the dialog non-modal or crash the callback.
"""

import tkinter as tk


def fit_to_content(window, min_w=0, min_h=0):
    """Ensure a Toplevel is large enough to show all its widgets (including the
    bottom buttons), and can't be shrunk below that. Grows only — an
    intentionally large dialog keeps its size. Call after the widgets are built.

    This matters at higher UI scaling: a hard-coded geometry like '780x540' that
    was fine at 1x can clip the bottom controls once fonts/widgets are scaled up,
    so the buttons only appear after the user manually enlarges the window."""
    try:
        window.update_idletasks()
        rw = max(window.winfo_reqwidth(), int(min_w))
        rh = max(window.winfo_reqheight(), int(min_h))
    except tk.TclError:
        return
    # honour any size already requested via geometry("WxH..."); grow, never shrink
    gw = gh = 0
    try:
        spec = window.geometry().split("+")[0]
        parts = spec.split("x")
        gw, gh = int(parts[0]), int(parts[1])
    except Exception:
        gw = gh = 0
    w, h = max(gw, rw), max(gh, rh)
    try:
        if (w, h) != (gw, gh):
            window.geometry("{}x{}".format(w, h))
        window.minsize(rw, rh)
    except tk.TclError:
        pass


def maximize(window, inset_w=0, inset_h=64):
    """Open `window` as large as the screen allows, right now.

    Window managers disagree about how to be told: Windows/some Linux WMs take
    `state("zoomed")`, others only `wm attributes -zoomed`, and ThinLinc's WM
    honours NEITHER (the same reason the old custom "Maximize" button did
    nothing). So we try both, check whether the window actually grew, and fall
    back to sizing it to the screen ourselves.

    The manual fallback leaves `inset_h` pixels of height (default 64) for the
    desktop panel/taskbar that would otherwise cover the window's bottom edge —
    exactly the "ThinLinc cuts off the lower half" problem. Returns True if the WM
    maximised it, False if we sized it by hand.
    """
    try:
        window.update_idletasks()
        sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
        before = window.winfo_width() * window.winfo_height()
    except tk.TclError:
        return False
    for attempt in (lambda: window.state("zoomed"),
                    lambda: window.attributes("-zoomed", True)):
        try:
            attempt()
            window.update_idletasks()
            if window.winfo_width() * window.winfo_height() > before * 1.2:
                return True
        except tk.TclError:
            continue
    try:
        w = max(640, sw - int(inset_w))
        h = max(480, sh - int(inset_h))
        # minsize may have been pinned by fit_to_content; never fight it
        window.geometry("{}x{}+0+0".format(w, h))
    except tk.TclError:
        pass
    return False


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
    # Make sure the dialog is big enough for its (possibly scaled) content first,
    # so bottom buttons aren't clipped behind a too-small hard-coded geometry.
    fit_to_content(window)
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
