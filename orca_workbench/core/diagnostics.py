"""Lightweight, opt-in runtime diagnostics for ORCA Workbench.

Why this exists: on the Lido gateway the app is shown over X11 forwarding, where
every Tk widget operation is a network round-trip — so a widget-heavy UI can feel
glacial even though the Python is fast. This module measures *where* the time
goes (per-phase wall-time, widget-count deltas, the X11 round-trip latency, and
home-dir filesystem latency) and writes a single .log on quit that is readable by
both a human and an AI.

It is disabled by default and effectively free when off (one flag check per
instrumented call). Enable it with `orca-workbench --diagnose`, or by setting the
environment variable ORCA_WORKBENCH_DIAG=1.
"""

import os
import sys
import time
import platform

_enabled = False
_root = None              # the Tk root, for widget counts + the X round-trip probe
_t0 = None                # perf_counter at enable() — the launch reference
_events = []              # list of dicts: one per timed phase / marker
_env = {}                 # captured environment + probe results
_status_cb = None         # optional callable(str): a live status-bar readout


def enable():
    # type: () -> None
    global _enabled, _t0
    _enabled = True
    if _t0 is None:
        _t0 = time.perf_counter()


def is_enabled():
    # type: () -> bool
    return _enabled


def set_status_callback(cb):
    # type: (object) -> None
    """Register a callable(str) shown live (e.g. in the status bar) per phase."""
    global _status_cb
    _status_cb = cb


def attach_root(root):
    # type: (object) -> None
    global _root
    _root = root


def _elapsed():
    # type: () -> float
    return 0.0 if _t0 is None else (time.perf_counter() - _t0)


def _count_widgets(w):
    # type: (object) -> int
    """Recursive count of Tk widget descendants. NOTE: Canvas *items* (used by
    the Workflow node editor) are not widgets, so a canvas-heavy phase shows few
    widgets but a large duration — that's expected; the timing still captures it."""
    if w is None:
        return 0
    try:
        kids = w.winfo_children()
    except Exception:
        return 0
    n = len(kids)
    for k in kids:
        n += _count_widgets(k)
    return n


def x_roundtrip_ms(n=100):
    # type: (int) -> float
    """Mean time of a forced X-server round-trip. winfo_pointerx() queries the
    server and is not cached, so this isolates display latency: ~microseconds on
    a local display, the network RTT (often 10-40 ms) over X11 forwarding."""
    if _root is None:
        return -1.0
    try:
        _root.update_idletasks()
        t = time.perf_counter()
        for _ in range(n):
            _root.winfo_pointerx()
        return (time.perf_counter() - t) / n * 1000.0
    except Exception:
        return -1.0


def home_stat_ms(n=50):
    # type: (int) -> float
    """Mean time to stat the home dir — a proxy for network-filesystem latency
    (the per-user config and project files live there on the cluster)."""
    home = os.path.expanduser("~")
    try:
        t = time.perf_counter()
        for _ in range(n):
            os.stat(home)
        return (time.perf_counter() - t) / n * 1000.0
    except Exception:
        return -1.0


def capture_environment():
    # type: () -> None
    """Snapshot platform/display/latency info. Safe to call more than once."""
    if not _enabled:
        return
    try:
        tkver = ""
        if _root is not None:
            try:
                tkver = str(_root.tk.call("info", "patchlevel"))
            except Exception:
                tkver = ""
        disp = os.environ.get("DISPLAY", "")
        # A local display is ":0"; a forwarded one has a host part ("localhost:10.0").
        looks_remote = bool(disp) and not disp.startswith(":")
        try:
            from orca_workbench.core import features
            mode = "simple" if features.is_simple() else "full"
        except Exception:
            mode = "?"
        _env.update({
            "mode": mode,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "tk": tkver,
            "display": disp or "(none)",
            "display_looks_remote": looks_remote,
            "home": os.path.expanduser("~"),
            "x_roundtrip_ms": round(x_roundtrip_ms(), 3),
            "home_stat_ms": round(home_stat_ms(), 3),
        })
    except Exception:
        pass


def record(label, dur_ms, w_before=None, w_after=None):
    # type: (str, float, object, object) -> None
    if not _enabled:
        return
    delta = None
    if w_before is not None and w_after is not None:
        delta = w_after - w_before
    _events.append({
        "t": round(_elapsed(), 3),
        "label": label,
        "dur_ms": round(dur_ms, 1),
        "w_after": w_after,
        "w_delta": delta,
    })
    if _status_cb is not None:
        try:
            msg = "diag: {} {:.0f} ms".format(label, dur_ms)
            if delta:
                msg += " ({:+d} widgets)".format(delta)
            _status_cb(msg)
        except Exception:
            pass


def mark(label):
    # type: (str) -> None
    """A zero-duration event marker (e.g. 'clicked Workflow tab')."""
    record(label, 0.0)


class timed(object):
    """Context manager that times a phase and records its widget-count delta.

        with diagnostics.timed("build:workflow"):
            ...build the tab...

    Near-zero overhead when diagnostics is disabled (a single flag check)."""
    __slots__ = ("label", "_t", "_w0")

    def __init__(self, label):
        # type: (str) -> None
        self.label = label
        self._t = None
        self._w0 = None

    def __enter__(self):
        if _enabled:
            self._w0 = _count_widgets(_root)
            self._t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if _enabled and self._t is not None:
            dur = (time.perf_counter() - self._t) * 1000.0
            record(self.label, dur, self._w0, _count_widgets(_root))
        return False


def write_log(path=None):
    # type: (object) -> str
    """Write the accumulated diagnostics to a .log file; return its path (or "")."""
    if not _enabled:
        return ""
    if not _env:
        capture_environment()
    if path is None:
        fname = "orca_workbench_diag_{}.log".format(time.strftime("%Y%m%d_%H%M%S"))
        path = os.path.join(os.path.expanduser("~"), fname)
    try:
        from orca_workbench import __version__ as _ver
    except Exception:
        _ver = "?"
    rtt = _env.get("x_roundtrip_ms", -1)
    lines = []
    lines.append("ORCA Workbench diagnostics log")
    lines.append("=" * 32)
    lines.append("generated:   " + time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("app version: " + str(_ver))
    lines.append("")
    lines.append("[environment]")
    for k in ("mode", "platform", "python", "tk", "display", "display_looks_remote",
              "home", "x_roundtrip_ms", "home_stat_ms"):
        lines.append("  {:<20} {}".format(k + ":", _env.get(k)))
    if isinstance(rtt, (int, float)) and rtt > 1.0:
        lines.append("  note: X11 round-trip > 1 ms => a remote/forwarded display.")
        lines.append("        Build/teardown phases scale with (widgets x this latency).")
    lines.append("")
    lines.append("[timeline]  elapsed_s | phase | duration_ms | widget_delta")
    for ev in _events:
        d = ev.get("w_delta")
        dstr = ("%+d" % d) if d else ""
        lines.append("  {:8.2f}  {:<30} {:>9.1f} ms  {}".format(
            ev["t"], ev["label"], ev["dur_ms"], dstr))
    lines.append("")
    lines.append("[summary]")
    if _events:
        slowest = sorted(_events, key=lambda e: e["dur_ms"], reverse=True)[:5]
        lines.append("  slowest phases:")
        for ev in slowest:
            lines.append("    {:>9.1f} ms  {}".format(ev["dur_ms"], ev["label"]))
        peak = max((e.get("w_after") or 0) for e in _events)
        lines.append("  peak widget count: ~{}".format(peak))
        if isinstance(rtt, (int, float)) and rtt > 0:
            lines.append("  rough cost of peak widgets x round-trip: ~{:.1f} s "
                         "(how much of the slowness is pure display latency)"
                         .format(peak * rtt / 1000.0))
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception as e:
        try:
            sys.stderr.write("diagnostics: could not write log: {}\n".format(e))
        except Exception:
            pass
        return ""
