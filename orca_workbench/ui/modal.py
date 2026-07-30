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


def screen_work_area(window):
    """(x, y, w, h) of the desktop area NOT covered by panels/taskbars.

    On X11 the window manager publishes this as the `_NET_WORKAREA` root
    property, which is the only reliable way to know how tall a window may be on
    a desktop with a top panel AND a bottom taskbar (a ThinLinc/MATE session has
    both). Falls back to the full screen when the property or `xprop` is missing
    (Windows, where the WM handles maximise correctly anyway)."""
    try:
        sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    except tk.TclError:
        return (0, 0, 1024, 768)
    try:
        if window.tk.call("tk", "windowingsystem") != "x11":
            return (0, 0, sw, sh)
    except tk.TclError:
        return (0, 0, sw, sh)
    try:
        import re
        import subprocess
        out = subprocess.check_output(["xprop", "-root", "_NET_WORKAREA"],
                                      stderr=subprocess.DEVNULL, timeout=3)
        nums = [int(t) for t in re.findall(r"\d+", out.decode("utf-8", "replace"))]
        # one x,y,w,h quad per virtual desktop — the current one is first
        if len(nums) >= 4 and nums[2] > 100 and nums[3] > 100:
            return (nums[0], nums[1], min(nums[2], sw), min(nums[3], sh))
    except Exception:
        pass
    return (0, 0, sw, sh)


def _clamp_into(window, area, passes=3):
    """Shrink/re-anchor `window` until it fits inside `area` = (x, y, w, h).

    Each pass measures where the window ACTUALLY ended up (`winfo_rootx/rooty`,
    which sit inside the WM's frame) and takes the overflow off the size we
    request, re-anchoring at the area's top-left. That accounts for the title
    bar and borders without having to know their thickness, and it converges in
    one or two passes — which is what stops the bottom edge (and the control row
    on it) from sitting under a taskbar.

    A window that already fits is left completely alone, so a genuine WM
    maximise keeps its maximised state."""
    ax, ay, aw, ah = area
    want = None
    for _ in range(max(1, passes)):
        try:
            window.update_idletasks()
            x, y = window.winfo_rootx(), window.winfo_rooty()
            w, h = window.winfo_width(), window.winfo_height()
        except tk.TclError:
            return
        over_w = (x + w) - (ax + aw)
        over_h = (y + h) - (ay + ah)
        if over_w <= 2 and over_h <= 2:
            return
        base_w, base_h = want if want else (w, h)
        want = (max(480, base_w - max(0, over_w)), max(360, base_h - max(0, over_h)))
        try:
            window.geometry("{}x{}+{}+{}".format(want[0], want[1], ax, ay))
        except tk.TclError:
            return


def maximize(window):
    """Open `window` as large as the desktop's usable area allows, right now.

    Window managers disagree about how to be told: Windows takes
    `state("zoomed")`, X11 WMs generally take `wm attributes -zoomed`, and some
    honour neither. So we try both, then — whatever happened — CLAMP the result
    into the real work area (see screen_work_area / _clamp_into), because a
    "maximised" window on a ThinLinc desktop still ends up taller than the space
    between the top panel and the taskbar, and its bottom rows are simply not
    reachable.

    Any minimum size pinned by fit_to_content is relaxed first: a minsize taller
    than the screen makes the window unshrinkable, which is how a window ends up
    hanging off the bottom in the first place. Returns True if the WM maximised
    it, False if we sized it by hand."""
    area = screen_work_area(window)
    try:
        window.update_idletasks()
        before = window.winfo_width() * window.winfo_height()
        window.minsize(min(560, area[2]), min(360, area[3]))
    except tk.TclError:
        return False
    wm_did_it = False
    for attempt in (lambda: window.state("zoomed"),
                    lambda: window.attributes("-zoomed", True)):
        try:
            attempt()
            window.update_idletasks()
            if window.winfo_width() * window.winfo_height() > before * 1.2:
                wm_did_it = True
                break
        except tk.TclError:
            continue
    if not wm_did_it:
        try:
            window.geometry("{}x{}+{}+{}".format(area[2], area[3], area[0], area[1]))
        except tk.TclError:
            pass
    _clamp_into(window, area)
    return wm_did_it


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
