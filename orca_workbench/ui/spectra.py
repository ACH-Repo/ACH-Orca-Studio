"""Simulated spectrum windows (matplotlib embedded in Tk): IR, UV-Vis, NMR, EPR, ENDOR.

All five share ONE foundation, `BaseSpectrumWindow`, which owns every bit of window
chrome — a non-modal Toplevel with real WM decorations (the standard maximize button
works), an embedded matplotlib navigation toolbar (Home/Pan/Zoom/Save), a slim custom
control row plus a stack y-offset slider, the canvas, and a colour-matched hover
structure panel — so a subclass only describes its data and how to draw its traces.
Fix a layout / maximise / hover / stacking issue in the base once, not five times.

matplotlib is a soft dependency (already needed for live plots); structure images
additionally need matplotlib's PNG reader (Pillow). Everything degrades gracefully if
those are missing.
"""

import io
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from orca_workbench.core import spectra as S
from orca_workbench.core import epr as EPR_sim
from orca_workbench.ui.depict import render_smiles_png
from orca_workbench.ui.modal import fit_to_content, make_modal


def _load_mpl():
    """Return the core matplotlib pieces or raise with a friendly message."""
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    return Figure, FigureCanvasTkAgg


def _load_mpl_full():
    """matplotlib pieces for BaseSpectrumWindow, or raise with a message. No navigation
    toolbar — pan/zoom/save are keyboard-driven (over ThinLinc the Linux desktop panel
    draws on top of the toolbar and it can't be reached)."""
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    return Figure, FigureCanvasTkAgg, None


def pin_device_pixel_ratio(canvas):
    """Stop matplotlib from sizing the figure by the global Tk 'scaling' factor.

    On HiDPI Windows, matplotlib derives the figure's device-pixel-ratio from
    `tk scaling` (which we set larger for readable fonts), so the figure renders
    ~1.2–1.5× too big and overflows the window — and a recompute on the next
    <Configure> makes it grow a moment after opening. Pinning the ratio to 1.0
    means the figure size is driven purely by the Tk widget size (matplotlib's
    own resize handler fits it), independent of the UI font scaling."""
    try:
        orig = canvas._set_device_pixel_ratio
        canvas._set_device_pixel_ratio = lambda ratio, _o=orig: _o(1.0)
        canvas._set_device_pixel_ratio(1.0)   # apply now
    except Exception:
        pass


def _mpl_unavailable_window(parent, err):
    top = tk.Toplevel(parent)
    top.title("Plotting unavailable")
    msg = ("Could not initialise matplotlib:\n  {}\n\n"
           "Install it on the cluster with:\n  pip install --user matplotlib\n"
           "(and remember 'module load python' so numpy finds its libraries)."
           .format(err))
    ttk.Label(top, text=msg, justify=tk.LEFT, wraplength=520).pack(padx=20, pady=20)
    ttk.Button(top, text="Close", command=top.destroy).pack(pady=(0, 12))
    make_modal(top, parent)
    return top


def _crop_whitespace(arr):
    """Crop near-white / transparent border pixels from an image array so the
    structure sits tight in its box. Returns the (possibly) cropped array."""
    try:
        import numpy as np
    except Exception:
        return arr
    a = arr
    if a.ndim != 3:
        return arr
    rgb = a[:, :, :3]
    nonwhite = np.any(rgb < 0.95, axis=2)
    if a.shape[2] == 4:
        nonwhite = nonwhite & (a[:, :, 3] > 0.05)
    if not nonwhite.any():
        return arr
    rows = np.where(np.any(nonwhite, axis=1))[0]
    cols = np.where(np.any(nonwhite, axis=0))[0]
    pad = 2
    r0 = max(0, rows[0] - pad); r1 = min(a.shape[0] - 1, rows[-1] + pad)
    c0 = max(0, cols[0] - pad); c1 = min(a.shape[1] - 1, cols[-1] + pad)
    return a[r0:r1 + 1, c0:c1 + 1]


def _whiten_to_transparent(arr):
    """Turn the near-white background into transparent pixels so the structure
    overlays the spectrum without blanking out the lines behind it."""
    try:
        import numpy as np
    except Exception:
        return arr
    if arr is None or arr.ndim != 3:
        return arr
    a = arr.astype(float)
    if a.max() > 1.5:           # 0–255 image → normalise to 0–1
        a = a / 255.0
    if a.shape[2] == 3:         # add an alpha channel
        a = np.dstack([a, np.ones(a.shape[:2])])
    white = np.all(a[:, :, :3] > 0.92, axis=2)
    a[white, 3] = 0.0
    return a


def _smiles_to_array(smiles, size=(320, 240), crop=True):
    """RDKit SMILES -> RGBA numpy array for matplotlib imshow, with the white
    background made transparent, or None."""
    if not smiles:
        return None
    png, err = render_smiles_png(smiles, size=size)
    if png is None:
        return None
    try:
        import matplotlib.image as mpimg
        arr = mpimg.imread(io.BytesIO(png), format="png")
    except Exception:
        return None
    if crop:
        try:
            arr = _crop_whitespace(arr)
        except Exception:
            pass
    try:
        arr = _whiten_to_transparent(arr)
    except Exception:
        pass
    return arr


# A consistent qualitative colour cycle for molecules.
_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
           "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


def _maximize_window(win):
    """Best-effort maximise a Toplevel across platforms / window managers. (The
    windows are non-modal with real decorations now, so the WM's own maximize
    button is the primary path; this stays as a programmatic fallback.)"""
    for attempt in (lambda: win.state("zoomed"),
                    lambda: win.attributes("-zoomed", True)):
        try:
            attempt()
            return
        except Exception:
            pass
    try:
        win.geometry("{}x{}+0+0".format(win.winfo_screenwidth(), win.winfo_screenheight()))
    except Exception:
        pass


class _StructurePanel(ttk.Frame):
    """One fixed-width side panel showing a SINGLE molecule's 2D structure + name,
    updated on hover. Exactly one structure is on screen at a time — whichever trace
    the cursor is over — and the image sits in a border coloured to match that trace."""

    _NEUTRAL = "#cccccc"

    def __init__(self, parent, width=300):
        ttk.Frame.__init__(self, parent, width=width)
        self.pack_propagate(False)
        self.name = ttk.Label(self, text="hover a peak", anchor="center",
                              font=("TkDefaultFont", 10, "bold"))
        self.name.pack(side=tk.TOP, fill=tk.X, pady=(10, 4), padx=6)
        # A tk.Frame (not ttk) so we can colour its border to match the hovered
        # trace — themed ttk frames don't expose a settable border colour.
        self.border = tk.Frame(self, highlightthickness=3, bd=0,
                               highlightbackground=self._NEUTRAL, highlightcolor=self._NEUTRAL)
        self.border.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.img = ttk.Label(self.border, anchor="center")
        self.img.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._cache = {}     # smiles -> PhotoImage (or None)
        self._cur = object()  # sentinel so the first show() always renders

    def _set_border(self, color):
        try:
            self.border.configure(highlightbackground=color, highlightcolor=color)
        except tk.TclError:
            pass

    def show(self, key, name, smiles, color="#000000"):
        if key == self._cur:
            return
        self._cur = key
        col = color or "#000000"
        self.name.configure(text=name or "", foreground=col)
        self._set_border(col)   # frame border matches the on-hover trace colour
        if smiles in self._cache:
            photo = self._cache[smiles]
        else:
            photo = None
            if smiles:
                from orca_workbench.ui.depict import smiles_to_photoimage
                photo, _ = smiles_to_photoimage(smiles, size=(280, 240), master=self.img)
            self._cache[smiles] = photo
        self.img.configure(image=photo or "")
        self.img.image = photo   # keep a ref so it isn't GC'd

    def clear(self):
        if self._cur is None:
            return
        self._cur = None
        self.name.configure(text="hover a peak", foreground="#000000")
        self._set_border(self._NEUTRAL)
        self.img.configure(image="")
        self.img.image = None


# --------------------------------------------------------------- the foundation

class BaseSpectrumWindow(tk.Toplevel):
    """Shared foundation for every spectrum window.

    Owns all the window chrome so subclasses only describe their data and drawing:
      * a NON-modal Toplevel with real WM decorations (the standard maximize button
        works; the main app stays usable; several plots can be open at once),
      * an embedded matplotlib navigation toolbar (Home / Pan / Zoom / Save),
      * a slim custom control row (subclass widgets, packed left) plus Redraw / Close
        and a stack y-offset slider (shown only when >1 trace), so the top bar can't
        overflow the window,
      * the canvas + a colour-matched hover structure panel,
      * per-trace vertical stacking with an adjustable offset, and the shared
        redraw / legend / structure / hover plumbing.

    Subclass contract: populate self.mols (a list of trace dicts, each with at least
    'name', 'short', 'color', 'smiles') in __init__, then call self._build_ui(...).
    Implement add_controls(bar) and plot(ax); optionally override add_summary(),
    after_plot(ax) and _on_motion(event) (the base provides _set_active / _clear_hover
    and the self.baseline() stacking helper)."""

    DEFAULT_GEOMETRY = "1150x720"
    # Subclass switches: a bar chart has no vertical-stack semantics and draws its
    # own legend, so it turns these off (no dead offset slider, no duplicate legend).
    SHOW_OFFSET = True
    AUTO_LEGEND = True

    def __init__(self, parent, window_title):
        super().__init__(parent)
        self.title(window_title)
        self.geometry(self.DEFAULT_GEOMETRY)
        self._parent = parent
        self.mols = []            # subclass fills this before _build_ui()
        self._stacked = False
        self._active = None
        self._hover_artists = []
        self.ax = None
        self.fig = None
        self.canvas = None
        self.struct = None
        self.offset_var = None
        self._home_xlim = None
        self._home_ylim = None
        self._nav_mode = None     # None | zoom_h/zoom_v/zoom_box | pan_h/pan_v/pan_free
        self._drag = None         # active zoom/pan drag state
        self._mpl_ok = True
        try:
            self._Figure, self._Canvas, self._NavToolbar = _load_mpl_full()
        except Exception as e:
            self._mpl_ok = False
            self.destroy()
            _mpl_unavailable_window(parent, e)

    # -------------------------------------------------------- construction

    def _build_ui(self, empty_message=None):
        """Assemble the toolbar + body once self.mols is populated. Returns False
        (after showing a small placeholder) when there's nothing to plot or mpl is
        unavailable, so the subclass __init__ can just `return`."""
        if not self._mpl_ok:
            return False
        if not self.mols:
            ttk.Label(self, text=empty_message or "Nothing to plot.").pack(padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
            fit_to_content(self)
            return False
        self._stacked = len(self.mols) > 1

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))
        self._bar = bar
        self.add_controls(bar)                       # subclass widgets (packed LEFT)
        # Shared right-hand controls. Created in tab order (offset -> Redraw -> Close)
        # but packed so they READ left-to-right offset | Redraw | Close — Tk's tab order
        # follows creation order, not visual position, which is why Close used to be
        # tabbed before Redraw.
        self.offset_var = tk.DoubleVar(value=0.0)
        off_label = off_scale = None
        if self._stacked and self.SHOW_OFFSET:
            off_label = ttk.Label(bar, text="Stack offset:")
            off_scale = ttk.Scale(bar, from_=0.0, to=1.5, orient=tk.HORIZONTAL, length=110,
                                  variable=self.offset_var, command=lambda _v: self._redraw())
        redraw_btn = ttk.Button(bar, text="Redraw", command=self._redraw)
        close_btn = ttk.Button(bar, text="Close", command=self.destroy)
        close_btn.pack(side=tk.RIGHT, padx=(2, 0))
        redraw_btn.pack(side=tk.RIGHT, padx=2)
        if self._stacked and self.SHOW_OFFSET:
            off_scale.pack(side=tk.RIGHT, padx=(0, 4))
            off_label.pack(side=tk.RIGHT, padx=(10, 2))

        self._add_limits_row()                       # compact x/y limit boxes + key hints
        self.add_summary()                           # optional subclass summary line

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.struct = _StructurePanel(body, width=300)
        self.struct.pack(side=tk.RIGHT, fill=tk.Y)
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fig = self._Figure(figsize=(8.6, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = self._Canvas(self.fig, master=left)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # No matplotlib navigation toolbar: over ThinLinc the Linux desktop panel
        # draws on top of it and it can't be reached, so we drive pan/zoom/save/etc.
        # entirely from keyboard shortcuts + our own zoom/pan (see _bind_plot_keys).
        self._disable_mpl_keymap()
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_press_event", self._drag_press)
        self.canvas.mpl_connect("button_release_event", self._drag_release)
        self._bind_plot_keys()

        fit_to_content(self)     # non-modal: keep real WM decorations (maximize button)
        self.after(0, self._first_draw)
        return True

    def _first_draw(self):
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        self._redraw()

    # ---------------------------------------------- limit boxes + shortcuts

    def _add_limits_row(self):
        """A compact second row of x/y limit boxes + Mestrenova-style key hints. The
        boxes give manual numeric limits (blank = auto); the keys (active while the
        pointer is over the plot) drive reset / manual-edit / zoom / pan."""
        row = ttk.Frame(self)
        row.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 2))
        self._lim = {}
        self._lim_entries = {}
        ttk.Label(row, text="View  x:").pack(side=tk.LEFT)
        for k in ("x0", "x1"):
            self._lim[k] = tk.StringVar()
            e = ttk.Entry(row, textvariable=self._lim[k], width=7)
            e.pack(side=tk.LEFT, padx=1)
            e.bind("<Return>", lambda _e: self._redraw())
            self._lim_entries[k] = e
        ttk.Label(row, text="y:").pack(side=tk.LEFT, padx=(6, 0))
        for k in ("y0", "y1"):
            self._lim[k] = tk.StringVar()
            e = ttk.Entry(row, textvariable=self._lim[k], width=7)
            e.pack(side=tk.LEFT, padx=1)
            e.bind("<Return>", lambda _e: self._redraw())
            self._lim_entries[k] = e
        ttk.Button(row, text="Apply", width=6, command=self._redraw).pack(side=tk.LEFT, padx=(3, 1))
        ttk.Label(row, text="   keys over plot:  F reset · M limits · Z zoom · P pan · "
                  "R redraw · Ctrl+S save · Ctrl+W close",
                  foreground="#777").pack(side=tk.LEFT, padx=8)
        self._mode_label = ttk.Label(row, text="", foreground="#0055aa",
                                     font=("TkDefaultFont", 9, "bold"))
        self._mode_label.pack(side=tk.LEFT, padx=6)

    def _lim_val(self, k):
        try:
            return float(self._lim[k].get())
        except (ValueError, TypeError, KeyError):
            return None

    def _apply_limit_boxes(self, ax):
        x0, x1 = self._lim_val("x0"), self._lim_val("x1")
        y0, y1 = self._lim_val("y0"), self._lim_val("y1")
        if x0 is not None or x1 is not None:
            cur = ax.get_xlim()
            ax.set_xlim(x0 if x0 is not None else cur[0], x1 if x1 is not None else cur[1])
        if y0 is not None or y1 is not None:
            cur = ax.get_ylim()
            ax.set_ylim(y0 if y0 is not None else cur[0], y1 if y1 is not None else cur[1])

    def _disable_mpl_keymap(self):
        """Drop matplotlib's default single-key bindings (f=fullscreen, p=pan, o=zoom,
        g=grid, ...) so our F/M/Z/P over the canvas aren't shadowed by them."""
        try:
            import matplotlib
            for k in ("keymap.fullscreen", "keymap.pan", "keymap.zoom", "keymap.grid",
                      "keymap.grid_minor", "keymap.xscale", "keymap.yscale",
                      "keymap.home", "keymap.save"):
                if k in matplotlib.rcParams:
                    matplotlib.rcParams[k] = []
        except Exception:
            pass

    _ZOOM_CYCLE = ["zoom_h", "zoom_v", "zoom_box", None]
    _PAN_CYCLE = ["pan_h", "pan_v", "pan_free", None]
    _MODE_TEXT = {"zoom_h": "ZOOM horizontal — drag a range (Esc to exit)",
                  "zoom_v": "ZOOM vertical — drag a range (Esc to exit)",
                  "zoom_box": "ZOOM box — drag a rectangle (Esc to exit)",
                  "pan_h": "PAN horizontal — drag (Esc to exit)",
                  "pan_v": "PAN vertical — drag (Esc to exit)",
                  "pan_free": "PAN free — drag (Esc to exit)"}
    _MODE_CURSOR = {"zoom_h": "sb_h_double_arrow", "zoom_v": "sb_v_double_arrow",
                    "zoom_box": "tcross", "pan_h": "sb_h_double_arrow",
                    "pan_v": "sb_v_double_arrow", "pan_free": "fleur"}

    def _bind_action(self, widget, action_id, fn):
        """Bind a rebindable action's current key sequence(s) on `widget`. Read from the
        keymap at window-open time (rebinds apply to newly opened plots)."""
        from orca_workbench.core import keymap
        for seq in keymap.sequence_variants(keymap.sequence(action_id)):
            widget.bind(seq, lambda e, f=fn: (f(), "break")[1])

    def _bind_plot_keys(self):
        w = self.canvas.get_tk_widget()
        w.bind("<Enter>", lambda e: w.focus_set(), add="+")
        # Plain keys bind on the CANVAS so they fire only while the pointer is over the
        # plot (not while typing in the top fields). Sequences come from the keymap.
        self._bind_action(w, "plot.reset", self._key_full)
        self._bind_action(w, "plot.edit_limits", self._key_focus_limits)
        self._bind_action(w, "plot.zoom", lambda: self._cycle_nav(self._ZOOM_CYCLE))
        self._bind_action(w, "plot.pan", lambda: self._cycle_nav(self._PAN_CYCLE))
        self._bind_action(w, "plot.redraw", self._redraw)
        w.bind("<Escape>", lambda e: (self._set_nav_mode(None), "break")[1])
        # Modifier / function-key shortcuts bind window-wide (safe to fire off-canvas).
        self.bind("<F5>", lambda e: (self._redraw(), "break")[1])
        self._bind_action(self, "plot.save_image", self._key_save)
        self._bind_action(self, "plot.close", self.destroy)
        self.bind("<Return>", self._activate_focused)

    def _activate_focused(self, _event=None):
        """Enter presses the focused Button/Checkbutton (Tk only does this for Space by
        default). Entries/spinboxes keep their own Return handling (this no-ops there)."""
        w = None
        try:
            w = self.focus_get()
        except Exception:
            pass
        if isinstance(w, (ttk.Button, ttk.Checkbutton)):
            try:
                w.invoke()
            except Exception:
                pass
            return "break"

    def _key_save(self):
        if getattr(self, "fig", None) is not None:
            _save_figure(self.fig, self)

    def _key_focus_limits(self):
        e = self._lim_entries.get("x0")
        if e is not None:
            try:
                e.focus_set()
                e.select_range(0, tk.END)
            except tk.TclError:
                pass

    @staticmethod
    def _lims_close(a, b):
        span = abs(b[1] - b[0]) or 1.0
        return abs(a[0] - b[0]) < span * 1e-3 and abs(a[1] - b[1]) < span * 1e-3

    def _key_full(self):
        """Mestrenova 'F': two-stage reset. First reset X to the data view, then (if X
        is already there) reset Y too — clearing any manual limit boxes as it goes.
        Works whether the view was changed by the boxes or by our zoom/pan."""
        ax = self.ax
        if ax is None or not self._home_xlim:
            return
        if not self._lims_close(ax.get_xlim(), self._home_xlim):
            self._lim["x0"].set(""); self._lim["x1"].set("")
            ax.set_xlim(self._home_xlim)
            self.canvas.draw_idle()
        elif not self._lims_close(ax.get_ylim(), self._home_ylim):
            self._lim["y0"].set(""); self._lim["y1"].set("")
            ax.set_ylim(self._home_ylim)
            self.canvas.draw_idle()

    # -------------------------------------------------- custom zoom / pan (no toolbar)

    def _cycle_nav(self, cycle):
        cur = self._nav_mode if self._nav_mode in cycle else None
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else cycle[0]
        self._set_nav_mode(nxt)

    def _set_nav_mode(self, mode):
        self._drag = None
        self._nav_mode = mode
        try:
            self._mode_label.configure(text=("  " + self._MODE_TEXT[mode]) if mode else "")
        except (tk.TclError, KeyError):
            pass
        try:
            self.canvas.get_tk_widget().configure(cursor=self._MODE_CURSOR.get(mode, ""))
        except tk.TclError:
            pass

    def _set_axis(self, setter, getter, a, b):
        """Set an axis limit a..b preserving the axis's current direction (NMR/IR have
        a reversed x-axis, so we mustn't silently un-reverse it on zoom)."""
        lo, hi = min(a, b), max(a, b)
        cur = getter()
        setter((hi, lo) if cur[0] > cur[1] else (lo, hi))

    def _drag_press(self, event):
        if (not self._nav_mode or event.button != 1 or event.inaxes is not self.ax
                or event.xdata is None or event.ydata is None):
            return
        ax = self.ax
        # Capture the press-time data->pixel transform (inverse). Pan measures the drag
        # in PIXELS through this FIXED transform, not via event.xdata — xdata is derived
        # from the current limits, which the pan is changing, so using it feeds back and
        # makes the motion erratic/accelerating.
        self._drag = {"x0": event.xdata, "y0": event.ydata,
                      "xlim": ax.get_xlim(), "ylim": ax.get_ylim(),
                      "inv": ax.transData.inverted(), "mode": self._nav_mode}
        if self._nav_mode.startswith("zoom"):
            # Blitted rubber band: snapshot the plot once, then only re-blit the
            # rectangle on motion (no full redraw per event — ThinLinc-friendly).
            try:
                self.canvas.draw()
                self._drag["bg"] = self.canvas.copy_from_bbox(self.fig.bbox)
                from matplotlib.patches import Rectangle
                rect = Rectangle((event.xdata, event.ydata), 0, 0, fill=False,
                                 edgecolor="#333333", linewidth=1.0, linestyle="--")
                rect.set_animated(True)
                ax.add_patch(rect)
                self._drag["rect"] = rect
            except Exception:
                self._drag["rect"] = None

    def _drag_motion(self, event):
        d = self._drag
        if d is None or event.xdata is None or event.ydata is None:
            return
        ax = self.ax
        mode = d["mode"]
        if mode.startswith("pan"):
            inv = d.get("inv")
            if inv is None:
                return
            # Data coords of the cursor's CURRENT pixel under the PRESS transform, so
            # the mapping is fixed for the whole drag (no feedback -> smooth pan).
            cx, cy = inv.transform((event.x, event.y))
            dx, dy = d["x0"] - cx, d["y0"] - cy
            (x0, x1), (y0, y1) = d["xlim"], d["ylim"]
            if mode in ("pan_h", "pan_free"):
                ax.set_xlim(x0 + dx, x1 + dx)
            if mode in ("pan_v", "pan_free"):
                ax.set_ylim(y0 + dy, y1 + dy)
            self.canvas.draw_idle()
            return
        rect = d.get("rect")
        if rect is None:
            return
        x0, y0 = d["x0"], d["y0"]
        (xa, xb), (ya, yb) = ax.get_xlim(), ax.get_ylim()
        if mode == "zoom_h":
            rx0, rx1, ry0, ry1 = x0, event.xdata, min(ya, yb), max(ya, yb)
        elif mode == "zoom_v":
            rx0, rx1, ry0, ry1 = min(xa, xb), max(xa, xb), y0, event.ydata
        else:
            rx0, rx1, ry0, ry1 = x0, event.xdata, y0, event.ydata
        rect.set_bounds(min(rx0, rx1), min(ry0, ry1), abs(rx1 - rx0), abs(ry1 - ry0))
        try:
            self.canvas.restore_region(d["bg"])
            ax.draw_artist(rect)
            self.canvas.blit(self.fig.bbox)
        except Exception:
            self.canvas.draw_idle()

    def _drag_release(self, event):
        d = self._drag
        self._drag = None
        if d is None:
            return
        ax = self.ax
        mode = d["mode"]
        if mode.startswith("pan"):
            self.canvas.draw_idle()
            return
        rect = d.get("rect")
        if rect is not None:
            try:
                rect.remove()
            except Exception:
                pass
        xe = event.xdata if event.xdata is not None else d["x0"]
        ye = event.ydata if event.ydata is not None else d["y0"]
        x0, y0 = d["x0"], d["y0"]
        if mode in ("zoom_h", "zoom_box") and abs(xe - x0) > 1e-12:
            self._set_axis(ax.set_xlim, ax.get_xlim, x0, xe)
        if mode in ("zoom_v", "zoom_box") and abs(ye - y0) > 1e-12:
            self._set_axis(ax.set_ylim, ax.get_ylim, y0, ye)
        self.canvas.draw_idle()

    # ------------------------------------------------------------ stacking

    def _offset_frac(self):
        try:
            return float(self.offset_var.get()) if self.offset_var is not None else 0.0
        except Exception:
            return 0.0

    def baseline(self, i, ref_amplitude):
        """Vertical baseline for trace i: i * offset_fraction * reference amplitude.
        0 for every trace when the slider is at 0 (plain overlay)."""
        return i * self._offset_frac() * (ref_amplitude or 1.0)

    def stack_top(self, ref_amplitude):
        """Baseline of the topmost stacked trace (0 when not offset)."""
        return self.baseline(len(self.mols) - 1, ref_amplitude)

    # Draw ordering for stacked traces. A y-offset waterfall should read
    # front-to-back like Mestrenova: the BOTTOM trace (i=0) sits in FRONT and the
    # higher-offset traces recede BEHIND it. matplotlib draws higher zorder on top,
    # so trace 0 gets the highest value and trace N-1 the lowest (2 = mpl's default
    # line z, still above the grid). Hover/label artists use HOVER_Z to stay on top.
    HOVER_Z = 50

    def _trace_zorder(self, i):
        return 2 + (len(self.mols) - 1 - i)

    # ------------------------------------------------------ redraw skeleton

    def _redraw(self):
        if not getattr(self, "fig", None):
            return
        try:
            self.fig.clear()
            self._hover_artists = []
            self._active = None
            ax = self.fig.add_subplot(111)
            self.ax = ax
            self.plot(ax)
            # Capture the data-driven view as 'home' (for the F reset) BEFORE any
            # manual limit-box overrides are applied on top.
            self._home_xlim = ax.get_xlim()
            self._home_ylim = ax.get_ylim()
            self._apply_limit_boxes(ax)
            if self._stacked:
                if self.AUTO_LEGEND:
                    # Fixed location, NOT 'best': loc='best' recomputes the legend
                    # position on every draw, so it hops around (and slows redraws)
                    # during a pan.
                    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
            else:
                m = self.mols[0]
                self.struct.show(0, m["name"], m.get("smiles"), m["color"])
            self.after_plot(ax)
            self.canvas.draw()
        except Exception:
            import traceback
            traceback.print_exc()

    # --------------------------------------------------------- hover helpers

    def _clear_hover(self):
        for a in self._hover_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._hover_artists = []

    def _set_active(self, mi):
        if mi == self._active:
            return
        self._active = mi
        if mi is None:
            if self._stacked:
                self.struct.clear()
        else:
            m = self.mols[mi]
            self.struct.show(mi, m["name"], m.get("smiles"), m["color"])

    # --------------------------------------------------------- subclass hooks

    def add_controls(self, bar):
        raise NotImplementedError

    def plot(self, ax):
        raise NotImplementedError

    def add_summary(self):
        pass

    def after_plot(self, ax):
        pass

    def _on_motion(self, event):
        # Single connected motion callback: route to an active zoom/pan drag,
        # otherwise to the subclass hover handler.
        if self._drag is not None:
            self._drag_motion(event)
            return
        self._hover(event)

    def _hover(self, event):
        pass


# --------------------------------------------------------------------------- IR

class IRSpectrumWindow(BaseSpectrumWindow):
    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        # entries: [{name, smiles, centers, intensities, freqs}] — one or more,
        # stacked as colour-matched traces.
        super().__init__(parent, "IR spectrum - {}".format(title))
        if not self._mpl_ok:
            return
        for idx, e in enumerate(entries):
            pairs = [(f, i) for f, i in zip(e["centers"], e["intensities"]) if abs(f) > 1.0]
            if not pairs:
                continue
            all_freqs = e.get("freqs") or e["centers"]
            imag = sorted(f for f in all_freqs if abs(f) > 1.0 and f < 0)
            self.mols.append({
                "name": e["name"],
                "short": e["name"].split(" / ")[0][:18],
                "color": _COLORS[idx % len(_COLORS)],
                "centers": [p[0] for p in pairs],
                "intens": [p[1] for p in pairs],
                "smiles": e.get("smiles"),
                "imag": imag,
            })
        self.fwhm_var = tk.DoubleVar(value=20.0)
        self.sticks_var = tk.BooleanVar(value=False)   # sticks OFF by default
        self.mode_var = tk.StringVar(value="absorbance")
        self._ymax = 1.0
        self._build_ui("No vibrational modes with IR intensity found.")

    def add_summary(self):
        if sum(len(m["imag"]) for m in self.mols) == 0:
            summary, fg = "All vibrational frequencies >= 0 - genuine minima.", "#1a7a1a"
        else:
            bits = ["{}: {}".format(m["short"], ", ".join("{:.1f}".format(f) for f in m["imag"]))
                    for m in self.mols if m["imag"]]
            summary = "Imaginary frequency(ies) - NOT a minimum: " + "; ".join(bits)
            fg = "#b00000"
        ttk.Label(self, text=summary, foreground=fg).pack(side=tk.TOP, anchor=tk.W, padx=10)

    def add_controls(self, bar):
        ttk.Label(bar, text="FWHM (cm^-1):").pack(side=tk.LEFT)
        sp = ttk.Spinbox(bar, from_=2, to=80, increment=2, width=6, textvariable=self.fwhm_var,
                         command=self._redraw)
        sp.pack(side=tk.LEFT, padx=6)
        sp.bind("<Return>", lambda e: self._redraw())
        ttk.Checkbutton(bar, text="Stick lines", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)
        self.mode_btn = ttk.Button(bar, text="Show: Absorbance", command=self._toggle_mode)
        self.mode_btn.pack(side=tk.LEFT, padx=6)

    def _toggle_mode(self):
        self.mode_var.set("transmission" if self.mode_var.get() == "absorbance" else "absorbance")
        self.mode_btn.configure(text="Show: " + self.mode_var.get().capitalize())
        self._redraw()

    def _imax_all(self):
        return max((m.get("_imax", 1.0) for m in self.mols), default=1.0) or 1.0

    def _stick_span(self, it, base, transmission):
        """(y0, y1) for a stick of raw intensity `it` above baseline `base`.

        Absorbance: peak-normalised kernels mean an isolated line's broadened curve
        peaks at exactly `it`, so a stick of height `it` matches the curve and never
        overshoots it. (The old code scaled sticks to the SUMMED-curve max, which
        inflated wherever two lines overlapped — the 'sticks too tall' bug.)"""
        if transmission:
            frac = it / self._imax_all()
            return (base + 100.0, base + 100.0 * (1.0 - frac))
        return (base, base + it)

    def plot(self, ax):
        fwhm = max(1.0, float(self.fwhm_var.get()))
        all_centers = [c for m in self.mols for c in m["centers"]]
        lo, hi = S.auto_range(all_centers, min_pad=80.0)
        transmission = self.mode_var.get() == "transmission"
        self._transmission = transmission

        ymax = 0.0
        curves = []
        for m in self.mols:
            xs, ys = S.broaden(m["centers"], m["intens"], lo, hi, n=1500, fwhm=fwhm)
            m["_imax"] = max(m["intens"]) or 1.0
            curves.append((m, xs, ys))
            ymax = max(ymax, max(ys) if ys else 0.0)
        self._ymax = ymax or 1.0
        ref = 100.0 if transmission else self._ymax

        for i, (m, xs, ys) in enumerate(curves):
            base = self.baseline(i, ref)
            m["_base"] = base
            m["_z"] = self._trace_zorder(i)
            if transmission:
                ypk = max(ys) or 1.0
                tvals = [base + 100.0 * (1.0 - y / ypk) for y in ys]
                ax.plot(xs, tvals, color=m["color"], lw=0.8, label=m["short"], zorder=m["_z"])
            else:
                ax.plot(xs, [y + base for y in ys], color=m["color"], lw=0.8,
                        label=m["short"], zorder=m["_z"])

        if self.sticks_var.get():
            for i, (m, xs, ys) in enumerate(curves):
                base = m["_base"]
                for c, it in zip(m["centers"], m["intens"]):
                    y0, y1 = self._stick_span(it, base, transmission)
                    ax.vlines(c, y0, y1, color=m["color"], linewidth=0.5, alpha=0.6,
                              zorder=m["_z"])

        if transmission:
            ax.set_ylim(-2.0, 102.0 + self.stack_top(ref))
            ax.set_ylabel("transmittance (%)")
        else:
            ax.set_ylim(0, (self._ymax + self.stack_top(ref)) * 1.12)
            ax.set_ylabel("IR absorbance (a.u.)")

        ax.set_xlim(hi, lo)   # IR convention: high wavenumber on the left
        ax.set_xlabel(r"wavenumber (cm$^{-1}$)")
        ax.set_title("Simulated IR spectrum")

    def _hover(self, event):
        ax = self.ax
        if ax is None:
            return
        if event.inaxes is not ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover(); self.canvas.draw_idle()
            if self._stacked:
                self._set_active(None)
            return
        x0, x1 = ax.get_xlim()
        tol = abs(x1 - x0) * 0.01 or 5.0
        best, best_d = None, 1e9
        for mi, m in enumerate(self.mols):
            for c in m["centers"]:
                d = abs(c - event.xdata)
                if d < best_d:
                    best_d, best = d, mi
        self._clear_hover()
        if best is None or best_d > tol:
            if self._stacked:
                self._set_active(None)
            self.canvas.draw_idle()
            return
        m = self.mols[best]
        near = [(c, it) for c, it in zip(m["centers"], m["intens"]) if abs(c - event.xdata) <= tol]
        # On-hover sticks only make sense for a single spectrum; skip in stacked view.
        if not self._stacked:
            for c, it in near:
                y0, y1 = self._stick_span(it, m.get("_base", 0.0), self._transmission)
                self._hover_artists.append(
                    ax.vlines(c, y0, y1, color=(0.05, 0.05, 0.05), linewidth=0.9,
                              zorder=self.HOVER_Z))
        near_sorted = sorted(near, key=lambda t: t[0], reverse=True)
        text = "\n".join("{:.1f}   I={:.1f}".format(c, it) for c, it in near_sorted)
        self._hover_artists.append(ax.annotate(
            text, xy=(0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
            fontsize=9, family="monospace", zorder=self.HOVER_Z,
            bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        if self._stacked:
            self._set_active(best)
        self.canvas.draw_idle()


# ------------------------------------------------------------------------ UV-Vis

_EV_NM = 1239.841984   # eV*nm: E[eV] = _EV_NM / lambda[nm]


class UVVisSpectrumWindow(BaseSpectrumWindow):
    """Simulated UV-Vis absorption from one or more TD-DFT calcs: oscillator-strength
    sticks Gaussian-broadened into bands, x-axis in nm (default) or eV, several
    molecules stacked as colour-matched traces with a hover structure panel."""

    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        # entries: [{name, smiles, states:[{wavelength_nm, energy_eV, fosc}]}]
        super().__init__(parent, "UV-Vis spectrum - {}".format(title))
        if not self._mpl_ok:
            return
        for idx, e in enumerate(entries):
            states = [s for s in e.get("states", []) if s.get("wavelength_nm")]
            if not states:
                continue
            self.mols.append({
                "name": e["name"],
                "short": e["name"].split(" / ")[0][:18],
                "color": _COLORS[idx % len(_COLORS)],
                "nm": [s["wavelength_nm"] for s in states],
                "ev": [s.get("energy_eV") or (_EV_NM / s["wavelength_nm"]) for s in states],
                "fosc": [s.get("fosc") or 0.0 for s in states],
                "smiles": e.get("smiles"),
            })
        self.axis_var = tk.StringVar(value="nm")
        self.fwhm_var = tk.DoubleVar(value=20.0)
        self.sticks_var = tk.BooleanVar(value=True)
        self._ymax = 1.0
        self._build_ui("No excited states found in the selected calculation(s).")

    def add_controls(self, bar):
        ttk.Label(bar, text="x-axis:").pack(side=tk.LEFT)
        self.axis_btn = ttk.Button(bar, text="Wavelength (nm)", command=self._toggle_axis)
        self.axis_btn.pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text="FWHM:").pack(side=tk.LEFT, padx=(10, 0))
        sp = ttk.Spinbox(bar, from_=1, to=200, increment=1, width=6, textvariable=self.fwhm_var,
                         command=self._redraw)
        sp.pack(side=tk.LEFT, padx=6)
        sp.bind("<Return>", lambda e: self._redraw())
        ttk.Checkbutton(bar, text="Stick lines", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)

    def _centers(self, m):
        return m["nm"] if self.axis_var.get() == "nm" else m["ev"]

    def _toggle_axis(self):
        if self.axis_var.get() == "nm":
            self.axis_var.set("ev")
            self.axis_btn.configure(text="Energy (eV)")
            self.fwhm_var.set(0.3)
        else:
            self.axis_var.set("nm")
            self.axis_btn.configure(text="Wavelength (nm)")
            self.fwhm_var.set(20.0)
        self._redraw()

    def plot(self, ax):
        fwhm = max(0.001, float(self.fwhm_var.get()))
        nm_axis = self.axis_var.get() == "nm"
        all_centers = [c for m in self.mols for c in self._centers(m)]
        lo, hi = S.auto_range(all_centers, min_pad=(30.0 if nm_axis else 0.5))
        if nm_axis:
            lo = max(0.0, lo)

        ymax = 0.0
        curves = []
        for m in self.mols:
            xs, ys = S.broaden(self._centers(m), m["fosc"], lo, hi, n=1400, fwhm=fwhm,
                               shape="gaussian")
            m["_fmax"] = max(m["fosc"]) or 1.0
            curves.append((m, xs, ys))
            ymax = max(ymax, max(ys) if ys else 0.0)
        self._ymax = ymax or 1.0
        ref = self._ymax

        for i, (m, xs, ys) in enumerate(curves):
            base = self.baseline(i, ref)
            m["_base"] = base
            m["_z"] = self._trace_zorder(i)
            ax.plot(xs, [y + base for y in ys], color=m["color"], lw=1.0,
                    label=m["short"], zorder=m["_z"])

        if self.sticks_var.get():
            for m in self.mols:
                base = m["_base"]
                for c, f in zip(self._centers(m), m["fosc"]):
                    if f <= 0:
                        continue
                    ax.vlines(c, base, base + self._ymax * (f / m["_fmax"]),
                              color=m["color"], linewidth=0.6, alpha=0.6,
                              zorder=m.get("_z", 2))

        ax.set_xlim(lo, hi)
        ax.set_ylim(0, (self._ymax + self.stack_top(ref)) * 1.12)
        ax.set_xlabel("wavelength (nm)" if nm_axis else "energy (eV)")
        ax.set_ylabel("oscillator strength / absorbance (a.u.)")
        ax.set_title("Simulated UV-Vis spectrum")

    def _hover(self, event):
        ax = self.ax
        if ax is None:
            return
        if event.inaxes is not ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover(); self.canvas.draw_idle()
            if self._stacked:
                self._set_active(None)
            return
        x0, x1 = ax.get_xlim()
        tol = abs(x1 - x0) * 0.012 or 1.0
        best, best_d = None, 1e9
        for mi, m in enumerate(self.mols):
            for c in self._centers(m):
                d = abs(c - event.xdata)
                if d < best_d:
                    best_d, best = d, mi
        self._clear_hover()
        if best is None or best_d > tol:
            if self._stacked:
                self._set_active(None)
            self.canvas.draw_idle()
            return
        m = self.mols[best]
        unit = "nm" if self.axis_var.get() == "nm" else "eV"
        near = [(c, f) for c, f in zip(self._centers(m), m["fosc"])
                if abs(c - event.xdata) <= tol]
        if not self._stacked:
            base = m.get("_base", 0.0)
            for c, f in near:
                if f <= 0:
                    continue
                self._hover_artists.append(
                    ax.vlines(c, base, base + self._ymax * (f / m["_fmax"]),
                              color=(0.05, 0.05, 0.05), linewidth=0.9, zorder=self.HOVER_Z))
        text = "\n".join("{:.1f} {}   f={:.3f}".format(c, unit, f)
                         for c, f in sorted(near, key=lambda t: t[0]))
        if text:
            self._hover_artists.append(ax.annotate(
                text, xy=(0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
                fontsize=9, family="monospace", zorder=self.HOVER_Z,
                bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        if self._stacked:
            self._set_active(best)
        self.canvas.draw_idle()


# -------------------------------------------------------------------------- NMR

# Common NMR-active isotopes per element (most-used first). Shielding is
# isotope-independent (electronic), but the *reference* and the conventional
# label are isotope-specific, so we let the user pick the isotope explicitly to
# avoid conflating, say, 14N and 15N scales.
NMR_ISOTOPES = {
    "H": ["1H", "2H"], "He": ["3He"], "Li": ["7Li", "6Li"], "Be": ["9Be"],
    "B": ["11B", "10B"], "C": ["13C"], "N": ["15N", "14N"], "O": ["17O"],
    "F": ["19F"], "Ne": ["21Ne"], "Na": ["23Na"], "Mg": ["25Mg"], "Al": ["27Al"],
    "Si": ["29Si"], "P": ["31P"], "S": ["33S"], "Cl": ["35Cl", "37Cl"],
    "K": ["39K"], "Ca": ["43Ca"], "Sc": ["45Sc"], "Ti": ["47Ti", "49Ti"],
    "V": ["51V"], "Mn": ["55Mn"], "Fe": ["57Fe"], "Co": ["59Co"],
    "Cu": ["63Cu", "65Cu"], "Zn": ["67Zn"], "Se": ["77Se"], "Br": ["79Br", "81Br"],
    "Rh": ["103Rh"], "Ag": ["109Ag", "107Ag"], "Cd": ["113Cd", "111Cd"],
    "Sn": ["119Sn", "117Sn"], "Te": ["125Te", "123Te"], "I": ["127I"],
    "Pt": ["195Pt"], "Hg": ["199Hg"], "Pb": ["207Pb"],
}


def isotopes_for(element):
    # type: (str) -> List[str]
    return NMR_ISOTOPES.get(element, [element])


def element_of(isotope_label):
    # type: (str) -> str
    return isotope_label.lstrip("0123456789") or isotope_label


class NMROptionsDialog(tk.Toplevel):
    """Ask which isotope and reference shielding to use. self.result is
    (element, isotope_label, reference_or_None) or None if cancelled."""

    def __init__(self, parent, elements):
        # type: (tk.Misc, List[str]) -> None
        super().__init__(parent)
        self.title("Plot NMR")
        self.resizable(False, False)
        self.result = None

        # Build the isotope choice list from the elements actually present.
        isos = []
        for el in elements:
            isos.extend(isotopes_for(el))
        if not isos:
            isos = ["1H"]

        ttk.Label(self, text="Plot a simulated NMR spectrum.", font=("TkDefaultFont", 10, "bold")
                  ).pack(anchor=tk.W, padx=12, pady=(12, 6))

        row1 = ttk.Frame(self)
        row1.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(row1, text="Nucleus (isotope):").pack(side=tk.LEFT)
        self.iso_var = tk.StringVar(value=isos[0])
        ttk.Combobox(row1, textvariable=self.iso_var, values=isos, state="readonly",
                     width=8).pack(side=tk.LEFT, padx=6)

        row2 = ttk.Frame(self)
        row2.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(row2, text="Reference shielding σ_ref (ppm):").pack(side=tk.LEFT)
        self.ref_var = tk.StringVar(value="")
        ent = ttk.Entry(row2, textvariable=self.ref_var, width=12)
        ent.pack(side=tk.LEFT, padx=6)
        ttk.Label(self, text="δ = σ_ref − σ. Leave blank to plot raw shielding σ instead.",
                  foreground="#666", wraplength=420, justify=tk.LEFT).pack(anchor=tk.W, padx=12)

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Plot", command=self._ok).pack(side=tk.RIGHT, padx=4)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        make_modal(self, parent)
        self.wait_window()

    def _ok(self):
        ref_txt = self.ref_var.get().strip()
        ref = None
        if ref_txt:
            try:
                ref = float(ref_txt)
            except ValueError:
                messagebox.showerror("Bad reference", "Reference must be a number, or blank.",
                                     parent=self)
                return
        iso = self.iso_var.get()
        self.result = (element_of(iso), iso, ref)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class NMRSpectrumWindow(BaseSpectrumWindow):
    """entries: list of dicts with keys name, smiles, shieldings (floats for the
    chosen element). nucleus_label e.g. '1H' is for display; reference sets the axis
    (δ vs raw σ)."""

    def __init__(self, parent, entries, nucleus_label, reference):
        super().__init__(parent, "NMR spectrum - {}".format(nucleus_label))
        if not self._mpl_ok:
            return
        for idx, e in enumerate(entries):
            shifts = [S.nmr_shift(s, reference) for s in e["shieldings"]]
            if not shifts:
                continue
            name = e["name"]
            self.mols.append({
                "name": name,
                "short": name.split(" / ")[0][:18],
                "color": _COLORS[idx % len(_COLORS)],
                "shifts": shifts,
                "smiles": e.get("smiles"),
            })
        self.nucleus_label = nucleus_label
        self.reference = reference
        self.fwhm_var = tk.DoubleVar(value=0.2)
        self.labels_var = tk.BooleanVar(value=False)   # peak-shift labels (off by default)
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self._user_xlim = None
        self._fwhm = 0.2
        self._last_hover = object()   # sentinel so the first hover always draws
        self._build_ui("No {} nuclei found in the selected calculations.".format(nucleus_label))

    def add_controls(self, bar):
        ttk.Label(bar, text="FWHM (ppm):").pack(side=tk.LEFT)
        sp = ttk.Spinbox(bar, from_=0.02, to=5.0, increment=0.05, width=6, textvariable=self.fwhm_var,
                         command=self._redraw)
        sp.pack(side=tk.LEFT, padx=6)
        sp.bind("<Return>", lambda e: self._redraw())
        ttk.Checkbutton(bar, text="Peak labels", variable=self.labels_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(bar, text="x-range:").pack(side=tk.LEFT, padx=(12, 2))
        e1 = ttk.Entry(bar, textvariable=self.xmin_var, width=7); e1.pack(side=tk.LEFT)
        ttk.Label(bar, text="to").pack(side=tk.LEFT, padx=2)
        e2 = ttk.Entry(bar, textvariable=self.xmax_var, width=7); e2.pack(side=tk.LEFT)
        for e in (e1, e2):
            e.bind("<Return>", lambda ev: self._apply_xrange())
        ttk.Button(bar, text="Apply", command=self._apply_xrange).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Auto", command=self._auto_xrange).pack(side=tk.LEFT)

    def _auto_range_vals(self):
        all_shifts = [s for m in self.mols for s in m["shifts"]]
        lo, hi = S.auto_range(all_shifts, pad_frac=0.25, min_pad=2.0)
        # Don't open zoomed to a single line: enforce a minimum window so a tight
        # cluster of peaks shows context (Mestrenova-style), not one peak filling
        # the axis. The user can still type a custom x-range.
        MIN_SPAN = 10.0
        if hi - lo < MIN_SPAN:
            mid = 0.5 * (lo + hi)
            lo, hi = mid - MIN_SPAN / 2.0, mid + MIN_SPAN / 2.0
        return lo, hi

    def _apply_xrange(self):
        try:
            lo = float(self.xmin_var.get()); hi = float(self.xmax_var.get())
        except ValueError:
            messagebox.showerror("Bad range", "x-range needs two numbers.", parent=self)
            return
        self._user_xlim = (lo, hi)
        self._redraw()

    def _auto_xrange(self):
        self._user_xlim = None
        self.xmin_var.set(""); self.xmax_var.set("")
        self._redraw()

    def plot(self, ax):
        fwhm = max(0.01, float(self.fwhm_var.get()))
        self._fwhm = fwhm
        self._last_hover = object()   # a fresh draw re-arms the hover caption
        # Determine the view range first (user override, else auto), and broaden the
        # curves over that whole range (plus a margin) so they fill the axis rather
        # than stopping at the data's auto-range edges.
        if self._user_xlim is not None:
            view_lo, view_hi = min(self._user_xlim), max(self._user_xlim)
        else:
            view_lo, view_hi = self._auto_range_vals()
        margin = (view_hi - view_lo) * 0.05 or 0.5
        grid_lo, grid_hi = view_lo - margin, view_hi + margin
        self._view = (view_lo, view_hi)

        ymax = 0.0
        curves = []
        for m in self.mols:
            # Adaptive grid: sharp NMR lines stay crisp when zoomed no matter how
            # wide the ppm axis is (a fixed uniform grid under-samples them).
            grid = S.adaptive_grid(m["shifts"], grid_lo, grid_hi, fwhm)
            xs, ys = S.broaden_at(m["shifts"], None, grid, fwhm=fwhm)
            curves.append((m, xs, ys))
            if ys:
                ymax = max(ymax, max(ys))
        ref = ymax or 1.0
        for i, (m, xs, ys) in enumerate(curves):
            base = self.baseline(i, ref)
            m["_base"] = base
            m["_z"] = self._trace_zorder(i)
            ax.plot(xs, [y + base for y in ys], color=m["color"], lw=0.6,
                    label=m["short"], zorder=m["_z"])

        # Peak-shift labels (Mestrenova-style: the ppm value in a vertical caption
        # above each peak apex, coloured to its trace). Off by default; when off the
        # value still appears on hover (see _hover).
        if self.labels_var.get():
            for m in self.mols:
                for c in m["shifts"]:
                    if view_lo <= c <= view_hi or view_hi <= c <= view_lo:
                        self._draw_shift_label(ax, m, c, fwhm, bold=False)

        xlabel = ("chemical shift δ (ppm)" if self.reference is not None
                  else "isotropic shielding σ (ppm)")
        # NMR convention: high shift / low shielding on the left (axis reversed).
        ax.set_xlim(view_hi, view_lo)
        ax.set_ylim(0, (ref + self.stack_top(ref)) * 1.18)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("intensity (a.u.)")
        ax.set_title("Simulated {} NMR".format(self.nucleus_label))

    def _peak_apex(self, m, center, fwhm):
        """y of a peak's apex (baseline + the summed Lorentzian at that center)."""
        return m.get("_base", 0.0) + sum(S.lorentzian(center, c, fwhm) for c in m["shifts"])

    def _draw_shift_label(self, ax, m, center, fwhm, bold=False, hover=False):
        """A vertical ppm caption held a little above a peak apex, joined to the apex
        by a fine black leader line (Mestrenova-style): the offset keeps the number
        clear of the peak, the connector shows which peak it belongs to. The leader is
        always black/hairline regardless of trace colour. Persistent labels are drawn
        in plot(); hover labels go through _hover_artists so they clear on the next move."""
        apex = self._peak_apex(m, center, fwhm)
        art = ax.annotate("{:.2f}".format(center), xy=(center, apex),
                          xytext=(0, 16), textcoords="offset points", rotation=90,
                          ha="center", va="bottom", fontsize=9 if hover else 8,
                          color=m["color"], clip_on=False, zorder=self.HOVER_Z,
                          fontweight="bold" if bold else "normal",
                          arrowprops=dict(arrowstyle="-", color="black",
                                          linewidth=0.4, shrinkA=1.5, shrinkB=1.5))
        if hover:
            self._hover_artists.append(art)

    def after_plot(self, ax):
        # reflect the active x-range in the entry boxes (only when auto)
        if self._user_xlim is None and getattr(self, "_view", None):
            lo, hi = self._view
            self.xmin_var.set("{:.2f}".format(min(lo, hi)))
            self.xmax_var.set("{:.2f}".format(max(lo, hi)))

    def _hover(self, event):
        ax = self.ax
        if ax is None:
            return
        # Work in data units (ppm) so it's symmetric about each peak and independent
        # of the display's pixel scaling.
        best, best_center, best_d = None, None, 1e9
        if event.inaxes is ax and event.xdata is not None:
            x0, x1 = ax.get_xlim()
            tol = abs(x1 - x0) * 0.025 or 0.5
            for mi, m in enumerate(self.mols):
                for sh in m["shifts"]:
                    d = abs(sh - event.xdata)
                    if d < best_d:
                        best_d, best, best_center = d, mi, sh
            if best_d > tol:
                best, best_center = None, None
        # Only redraw when the hovered peak actually changes — over ThinLinc a
        # per-motion redraw storm lags the framebuffer (see the project's redraw rule).
        key = (best, best_center)
        if key == self._last_hover:
            return
        self._last_hover = key
        self._clear_hover()
        self._set_active(best)
        # When persistent labels are off, show the hovered peak's shift as a caption.
        if best is not None and not self.labels_var.get():
            self._draw_shift_label(ax, self.mols[best], best_center, self._fwhm,
                                   bold=True, hover=True)
        self.canvas.draw_idle()


_KNOWN_IMG_EXT = {".png", ".pdf", ".svg", ".svgz", ".jpg", ".jpeg",
                  ".tif", ".tiff", ".eps", ".ps"}


def _ask_image_format(parent):
    # type: (tk.Misc) -> Optional[str]
    """Tiny modal asking which image format to use (when the typed filename had no
    extension). Returns 'png'/'svg'/... or None."""
    top = tk.Toplevel(parent)
    top.title("Image format")
    top.resizable(False, False)
    ttk.Label(top, text="No file extension given — choose an image format:").pack(
        padx=12, pady=(12, 6))
    var = tk.StringVar(value="png")
    ttk.Combobox(top, textvariable=var, state="readonly", width=10,
                 values=["png", "pdf", "svg", "jpg", "tif", "eps"]).pack(padx=12, pady=4)
    result = {"v": None}

    def ok():
        result["v"] = var.get()
        top.destroy()

    btns = ttk.Frame(top)
    btns.pack(pady=10)
    ttk.Button(btns, text="Cancel", command=top.destroy).pack(side=tk.RIGHT, padx=4)
    ttk.Button(btns, text="Save", command=ok).pack(side=tk.RIGHT, padx=4)
    top.bind("<Return>", lambda e: ok())
    top.bind("<Escape>", lambda e: top.destroy())
    make_modal(top, parent)
    top.wait_window()
    return result["v"]


def _save_figure(fig, parent):
    # Kept for callers that want a custom save; the embedded matplotlib toolbar's
    # Save button is the primary path now.
    path = filedialog.asksaveasfilename(
        title="Save spectrum image",
        filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg"),
                   ("JPEG", "*.jpg"), ("TIFF", "*.tif"), ("All files", "*.*")],
        parent=parent)
    if not path:
        return
    ext = os.path.splitext(path)[1].lower()
    if ext not in _KNOWN_IMG_EXT:
        fmt = _ask_image_format(parent)
        if not fmt:
            return
        path = path + "." + fmt
    try:
        fig.savefig(path, dpi=200, bbox_inches="tight")
    except Exception as e:
        messagebox.showerror("Save failed", str(e), parent=parent)


# Standard CW-EPR microwave bands and their typical frequencies (GHz). There is no
# standard "Z band"; the ladder is L/S/C/X/K/Q/W (then D/G above W).
_EPR_BANDS = [("L", 1.0), ("S", 3.5), ("C", 6.0), ("X", 9.5),
              ("K", 24.0), ("Q", 34.0), ("W", 94.0)]


class EPRSpectrumWindow(BaseSpectrumWindow):
    """Simulated EPR spectra from one or more finished %eprnmr calcs, stacked as
    colour-matched traces. Isotropic (g_iso + A_iso) or anisotropic powder (principal
    g + A) via the mode toggle; the field-swept lineshape shown as 1st derivative
    (CW-EPR), absorption, or 2nd derivative; MW frequency by band or number. Hover a
    trace to read its g-value and see its structure. (spin-1/2 / 100%-abundance model
    — see core.epr.)"""

    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        # entries: [{name, smiles, epr}] (epr = a parse_epr() dict).
        super().__init__(parent, "EPR spectrum - {}".format(title))
        if not self._mpl_ok:
            return
        for idx, e in enumerate(entries):
            epr = e.get("epr") or {}
            g_iso = (epr.get("g_tensor") or {}).get("g_iso")
            if g_iso is None:
                continue
            gp = (epr.get("g_tensor") or {}).get("g") or [g_iso] * 3
            self.mols.append({
                "name": e.get("name") or title,
                "short": (e.get("name") or title).split(" / ")[0][:18],
                "color": _COLORS[idx % len(_COLORS)],
                "g_iso": g_iso, "g_principal": gp,
                "hyperfine": epr.get("hyperfine") or [], "smiles": e.get("smiles"),
            })
        self.freq_var = tk.DoubleVar(value=9.5)
        self.band_var = tk.StringVar(value="X (9.5 GHz)")
        self.lw_var = tk.DoubleVar(value=0.15)
        self.mode = "iso"
        self.disp_var = tk.StringVar(value="1st derivative")
        self.sticks_var = tk.BooleanVar(value=False)
        self._build_ui("No g-tensor found in the selected calculation(s).")

    def add_controls(self, bar):
        ttk.Label(bar, text="MW freq (GHz):").pack(side=tk.LEFT)
        sp1 = ttk.Spinbox(bar, from_=1, to=400, increment=0.5, width=7,
                          textvariable=self.freq_var, command=self._redraw)
        sp1.pack(side=tk.LEFT, padx=6)
        sp1.bind("<Return>", lambda e: self._redraw())
        band_cb = ttk.Combobox(bar, textvariable=self.band_var, width=12, state="readonly",
                               values=["{} ({:g} GHz)".format(nm, gh) for nm, gh in _EPR_BANDS])
        band_cb.pack(side=tk.LEFT, padx=(2, 0))
        band_cb.bind("<<ComboboxSelected>>", self._on_band)
        ttk.Label(bar, text="Linewidth (mT):").pack(side=tk.LEFT, padx=(10, 0))
        sp2 = ttk.Spinbox(bar, from_=0.01, to=10, increment=0.05, width=7,
                          textvariable=self.lw_var, command=self._redraw)
        sp2.pack(side=tk.LEFT, padx=6)
        sp2.bind("<Return>", lambda e: self._redraw())
        self.mode_btn = ttk.Button(bar, text="Mode: isotropic", width=22, command=self._toggle_mode)
        self.mode_btn.pack(side=tk.LEFT, padx=(10, 2))
        ttk.Label(bar, text="Show:").pack(side=tk.LEFT, padx=(10, 0))
        disp_cb = ttk.Combobox(bar, textvariable=self.disp_var, width=13, state="readonly",
                               values=["1st derivative", "Absorption", "2nd derivative"])
        disp_cb.pack(side=tk.LEFT, padx=(2, 0))
        disp_cb.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        ttk.Checkbutton(bar, text="Line markers", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)

    def _toggle_mode(self):
        if self.mode == "iso":
            self.mode = "powder"
            self.mode_btn.configure(text="Mode: powder (anisotropic)")
            self.lw_var.set(0.3)
        else:
            self.mode = "iso"
            self.mode_btn.configure(text="Mode: isotropic")
            self.lw_var.set(0.15)
        self._redraw()

    def _on_band(self, _event=None):
        s = self.band_var.get()
        for nm, gh in _EPR_BANDS:
            if s.startswith(nm + " "):
                self.freq_var.set(gh)
                break
        self._redraw()

    @staticmethod
    def _derivative(y, x):
        n = len(y)
        d = [0.0] * n
        for i in range(1, n - 1):
            dx = x[i + 1] - x[i - 1]
            d[i] = (y[i + 1] - y[i - 1]) / dx if dx else 0.0
        return d

    def _sim_for(self, m, freq, lw):
        if self.mode == "powder":
            return EPR_sim.powder_spectrum(m["g_principal"], m["hyperfine"],
                                           freq_GHz=freq, linewidth_mT=lw,
                                           n_theta=40, n_phi=80, npoints=2500)
        return EPR_sim.simulate(m["g_iso"], m["hyperfine"], freq_GHz=freq, linewidth_mT=lw)

    def _trace_of(self, sim):
        field = sim["field_mT"]
        disp = self.disp_var.get()
        if disp == "Absorption":
            return field, (sim.get("absorption") or sim["derivative"]), "absorption (a.u.)"
        if disp == "2nd derivative":
            return field, self._derivative(sim["derivative"], field), "2nd derivative (a.u.)"
        return field, sim["derivative"], "first-derivative absorption (a.u.)"

    def plot(self, ax):
        freq = max(0.1, float(self.freq_var.get()))
        lw = max(0.001, float(self.lw_var.get()))
        powder = self.mode == "powder"

        ylabel = "first-derivative absorption (a.u.)"
        traces = []
        ref = 0.0
        for m in self.mols:
            sim = self._sim_for(m, freq, lw)
            field, trace, ylabel = self._trace_of(sim)
            m["_field"], m["_trace"], m["_sim"] = field, trace, sim
            traces.append((m, field, trace))
            ref = max(ref, max((abs(v) for v in trace), default=0.0))
        ref = ref or 1.0

        # Give every trace a COMMON field range (+ extra tolerance) so widely-separated
        # resonances — e.g. different g at W-band — share one x-axis instead of each
        # line stopping at its own narrow window. Traces extend flat at their baseline.
        clo = min(f[0] for _, f, _ in traces)
        chi = max(f[-1] for _, f, _ in traces)
        extra = (chi - clo) * 0.10 or 1.0
        clo -= extra; chi += extra

        for i, (m, field, trace) in enumerate(traces):
            base = self.baseline(i, ref)
            m["_base"] = base
            m["_z"] = self._trace_zorder(i)
            ax.axhline(base, color="#dddddd" if base else "#cccccc", lw=0.5, zorder=1)
            xs = [clo] + list(field) + [chi]
            ys = [base] + [v + base for v in trace] + [base]
            # g_iso in the (colour-coded) legend label — replaces the old permanent
            # top-left text box, which would eventually collide with offset traces.
            ax.plot(xs, ys, color=m["color"], lw=1.0, zorder=m["_z"],
                    label="{}  g={:.4f}".format(m["short"], m["g_iso"]))

        # Line markers: draw for EVERY trace at its own baseline (works stacked too now),
        # each scaled to that trace's own peak so a marker never dwarfs the line.
        if self.sticks_var.get():
            for m in self.mols:
                base = m["_base"]
                trace = m["_trace"]
                sticks = m["_sim"].get("sticks") or []
                peak = max((abs(v) for v in trace), default=1.0) or 1.0
                imax = max((it for _, it in sticks), default=1.0) or 1.0
                for bc, inten in sticks:
                    ax.vlines(bc, base, base + peak * (inten / imax),
                              color="#888888", linewidth=0.6, alpha=0.6,
                              zorder=m.get("_z", 2))

        ax.set_xlim(clo, chi)
        ax.set_xlabel("magnetic field (mT)")
        ax.set_ylabel(ylabel)
        ax.set_title("Simulated {} EPR spectrum".format(
            "powder (anisotropic)" if powder else "isotropic"))
        if not self._stacked:
            self._single_annotation(ax, self.mols[0], freq, powder)

    def _single_annotation(self, ax, m, freq, powder):
        def _coupling_mhz(g):
            a = g["A"]
            return abs(sum(a) / 3.0) if isinstance(a, (list, tuple)) else abs(a)
        sim = m["_sim"]
        if powder:
            gp = m["g_principal"]
            txt = "g = [{:.5f}, {:.5f}, {:.5f}]\nB0(g_iso) = {:.1f} mT @ {:.2f} GHz".format(
                gp[0], gp[1], gp[2], sim["center_mT"], freq)
        else:
            txt = "g_iso = {:.5f}\nB0 = {:.1f} mT @ {:.2f} GHz".format(
                m["g_iso"], sim["center_mT"], freq)
        groups = sim.get("groups") or []
        if groups:
            txt += "\n" + "\n".join(
                "a({}) = {:.1f} MHz  x{}".format(g["element"], _coupling_mhz(g), g["count"])
                for g in groups[:8])
        ax.text(0.01, 0.99, txt, transform=ax.transAxes, va="top", ha="left",
                fontsize=8, family="monospace",
                bbox=dict(boxstyle="round", fc="white", ec="#cccccc", alpha=0.85))

    def _trace_val(self, m, x):
        f = m.get("_field")
        if not f or x is None or x < f[0] or x > f[-1] or f[-1] == f[0]:
            return None
        idx = int(round((x - f[0]) / (f[-1] - f[0]) * (len(f) - 1)))
        # include the trace's stacking baseline so the y-match works when offset
        return m["_trace"][min(len(f) - 1, max(0, idx))] + m.get("_base", 0.0)

    def _hover(self, event):
        ax = self.ax
        if ax is None:
            return
        if event.inaxes is not ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover(); self.canvas.draw_idle()
            self._set_active(None)
            return
        y = event.ydata if event.ydata is not None else 0.0
        best, best_d = None, 1e9
        for mi, m in enumerate(self.mols):
            v = self._trace_val(m, event.xdata)
            if v is None:
                continue
            if abs(v - y) < best_d:
                best_d, best = abs(v - y), mi
        self._clear_hover()
        if best is None:
            self._set_active(None)
            self.canvas.draw_idle()
            return
        m = self.mols[best]
        freq = max(0.1, float(self.freq_var.get()))
        g_cursor = (freq * 1000.0) / (EPR_sim.MHZ_PER_MT * event.xdata) if event.xdata else 0.0
        self._hover_artists.append(ax.annotate(
            "{}\ng_iso = {:.5f}\ng(cursor) = {:.5f}".format(m["short"], m["g_iso"], g_cursor),
            xy=(0.99, 0.02), xycoords="axes fraction", va="bottom", ha="right",
            fontsize=9, family="monospace", zorder=self.HOVER_Z,
            bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        if self._stacked:
            self._set_active(best)
        self.canvas.draw_idle()


class ENDORSpectrumWindow(BaseSpectrumWindow):
    """Simulated ENDOR spectra from one or more finished %eprnmr calcs — intensity vs
    RF frequency (MHz), stacked as colour-matched traces. Built from the SAME hyperfine
    data as EPR (no new calculation): each coupled nucleus gives lines at |nu_n +/- A/2|.
    Hover a trace to read its structure. (isotropic model — see core.epr.)"""

    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        super().__init__(parent, "ENDOR spectrum - {}".format(title))
        if not self._mpl_ok:
            return
        for idx, e in enumerate(entries):
            epr = e.get("epr") or {}
            g_iso = (epr.get("g_tensor") or {}).get("g_iso")
            hf = epr.get("hyperfine") or []
            if g_iso is None or not hf:
                continue
            self.mols.append({
                "name": e.get("name") or title,
                "short": (e.get("name") or title).split(" / ")[0][:18],
                "color": _COLORS[idx % len(_COLORS)],
                "g_iso": g_iso, "hyperfine": hf, "smiles": e.get("smiles"),
            })
        self.freq_var = tk.DoubleVar(value=9.5)
        self.band_var = tk.StringVar(value="X (9.5 GHz)")
        self.lw_var = tk.DoubleVar(value=0.3)
        self.disp_var = tk.StringVar(value="Absorption")
        self.sticks_var = tk.BooleanVar(value=False)
        self._build_ui("No hyperfine couplings found in the selected calculation(s) - "
                       "ENDOR needs computed A-tensors.")

    def add_controls(self, bar):
        ttk.Label(bar, text="MW freq (GHz):").pack(side=tk.LEFT)
        sp1 = ttk.Spinbox(bar, from_=1, to=400, increment=0.5, width=7,
                          textvariable=self.freq_var, command=self._redraw)
        sp1.pack(side=tk.LEFT, padx=6)
        sp1.bind("<Return>", lambda e: self._redraw())
        band_cb = ttk.Combobox(bar, textvariable=self.band_var, width=12, state="readonly",
                               values=["{} ({:g} GHz)".format(nm, gh) for nm, gh in _EPR_BANDS])
        band_cb.pack(side=tk.LEFT, padx=(2, 0))
        band_cb.bind("<<ComboboxSelected>>", self._on_band)
        ttk.Label(bar, text="Linewidth (MHz):").pack(side=tk.LEFT, padx=(10, 0))
        sp2 = ttk.Spinbox(bar, from_=0.02, to=20, increment=0.1, width=7,
                          textvariable=self.lw_var, command=self._redraw)
        sp2.pack(side=tk.LEFT, padx=6)
        sp2.bind("<Return>", lambda e: self._redraw())
        ttk.Label(bar, text="Show:").pack(side=tk.LEFT, padx=(10, 0))
        disp_cb = ttk.Combobox(bar, textvariable=self.disp_var, width=13, state="readonly",
                               values=["Absorption", "1st derivative"])
        disp_cb.pack(side=tk.LEFT, padx=(2, 0))
        disp_cb.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        ttk.Checkbutton(bar, text="Line markers", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)

    def _on_band(self, _event=None):
        s = self.band_var.get()
        for nm, gh in _EPR_BANDS:
            if s.startswith(nm + " "):
                self.freq_var.set(gh)
                break
        self._redraw()

    def plot(self, ax):
        freq = max(0.1, float(self.freq_var.get()))
        lw = max(0.001, float(self.lw_var.get()))
        deriv = self.disp_var.get() == "1st derivative"

        traces = []
        ref = 0.0
        for m in self.mols:
            sim = EPR_sim.endor_spectrum(m["g_iso"], m["hyperfine"],
                                         freq_GHz=freq, linewidth_MHz=lw)
            xs = sim["freq_MHz"]
            ys = sim["derivative"] if deriv else sim["absorption"]
            m["_freq"], m["_trace"], m["_sim"] = xs, ys, sim
            traces.append((m, xs, ys))
            ref = max(ref, max((abs(v) for v in ys), default=0.0))
        ref = ref or 1.0

        # Common RF range (+ tolerance) so stacked traces share one x-axis.
        clo = min(x[0] for _, x, _ in traces)
        chi = max(x[-1] for _, x, _ in traces)
        extra = (chi - clo) * 0.08 or 1.0
        clo -= extra; chi += extra

        for i, (m, xs, ys) in enumerate(traces):
            base = self.baseline(i, ref)
            m["_base"] = base
            m["_z"] = self._trace_zorder(i)
            if base or deriv:
                ax.axhline(base, color="#dddddd" if base else "#cccccc", lw=0.5, zorder=1)
            xx = [clo] + list(xs) + [chi]
            yy = [base] + [v + base for v in ys] + [base]
            ax.plot(xx, yy, color=m["color"], lw=1.0, label=m["short"], zorder=m["_z"])
        ax.set_xlim(clo, chi)

        if self.sticks_var.get():
            for m in self.mols:
                base = m["_base"]
                trace = m["_trace"]
                sticks = m["_sim"].get("sticks") or []
                peak = max((abs(v) for v in trace), default=1.0) or 1.0
                smax = max((it for _, it in sticks), default=1.0) or 1.0
                for fc, inten in sticks:
                    ax.vlines(fc, base, base + peak * (inten / smax),
                              color="#888888", linewidth=0.6, alpha=0.6,
                              zorder=m.get("_z", 2))

        ax.set_xlabel("RF frequency (MHz)")
        ax.set_ylabel("first-derivative (a.u.)" if deriv else "ENDOR intensity (a.u.)")
        ax.set_title("Simulated ENDOR spectrum")
        if not self._stacked:
            self._single_annotation(ax, self.mols[0])

    def _single_annotation(self, ax, m):
        lines = m["_sim"].get("lines") or []
        nuh = EPR_sim.nuclear_larmor_MHz("H", m["_sim"]["B0_mT"]) or 0.0
        rows = ["nu(1H) = {:.2f} MHz @ B0 = {:.0f} mT".format(nuh, m["_sim"]["B0_mT"])]
        for L in lines[:8]:
            rows.append("{}: A={:.1f}  ->  {:.2f}, {:.2f} MHz  x{}".format(
                L["element"], L["A_iso"], L["lines"][0], L["lines"][1], L["count"]))
        ax.text(0.01, 0.99, "\n".join(rows), transform=ax.transAxes, va="top", ha="left",
                fontsize=8, family="monospace",
                bbox=dict(boxstyle="round", fc="white", ec="#cccccc", alpha=0.85))

    def _trace_val(self, m, x):
        f = m.get("_freq")
        if not f or x is None or x < f[0] or x > f[-1] or f[-1] == f[0]:
            return None
        idx = int(round((x - f[0]) / (f[-1] - f[0]) * (len(f) - 1)))
        return m["_trace"][min(len(f) - 1, max(0, idx))] + m.get("_base", 0.0)

    def _hover(self, event):
        ax = self.ax
        if ax is None:
            return
        if event.inaxes is not ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover(); self.canvas.draw_idle()
            self._set_active(None)
            return
        y = event.ydata if event.ydata is not None else 0.0
        best, best_d = None, 1e9
        for mi, m in enumerate(self.mols):
            v = self._trace_val(m, event.xdata)
            if v is not None and abs(v - y) < best_d:
                best_d, best = abs(v - y), mi
        self._clear_hover()
        if best is None:
            self._set_active(None)
            self.canvas.draw_idle()
            return
        m = self.mols[best]
        self._hover_artists.append(ax.annotate(
            "{}\nRF = {:.2f} MHz".format(m["short"], event.xdata),
            xy=(0.99, 0.02), xycoords="axes fraction", va="bottom", ha="right",
            fontsize=9, family="monospace", zorder=self.HOVER_Z,
            bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        if self._stacked:
            self._set_active(best)
        self.canvas.draw_idle()


# ------------------------------------------------------------- SCF energy bars

# Display label -> internal mode for draw_scf_bars (see core/figures).
_SCF_MODES = [
    ("absolute (Eh)", "absolute"),
    ("delta per molecule (kcal/mol)", "rel_molecule"),
    ("delta vs global min (kcal/mol)", "rel_global"),
]


class SCFEnergyBarWindow(BaseSpectrumWindow):
    """Grouped bar chart of final SCF energies: one group of bars per molecule,
    one colour-coded bar per calculation type within each group. Hovering a bar
    shows that molecule's 2D structure in the side panel (same panel the spectrum
    windows use). The energy axis switches between absolute Eh and two relative
    (kcal/mol) views. The drawing itself lives in core/figures.draw_scf_bars, so the
    interactive chart and the FIGS_EXPORT image are pixel-for-pixel the same."""

    SHOW_OFFSET = False     # bars have no vertical-stack semantics
    AUTO_LEGEND = False     # draw_scf_bars draws its own (calc-type) legend

    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        # entries: [{molecule, name, smiles, calctype, energy(Eh)}] — one per
        # finished calc with a final energy.
        super().__init__(parent, "Final SCF energies - {}".format(title))
        if not self._mpl_ok:
            return
        from orca_workbench.core import figures as figures_mod
        self._figures = figures_mod
        self._groups, self._calctypes = figures_mod.scf_bar_groups(entries)
        self._bar_recs = []
        self._mol_index = {}
        for g in self._groups:
            self._mol_index[g["molecule"]] = len(self.mols)
            self.mols.append({
                "name": g["name"], "short": g["molecule"],
                "color": figures_mod.color_of(len(self.mols)),
                "smiles": g.get("smiles"),
            })
        self.mode_var = tk.StringVar(value=_SCF_MODES[0][0])
        self._build_ui("No final SCF energies found in the selected calculation(s).")

    def add_controls(self, bar):
        ttk.Label(bar, text="Energy axis:").pack(side=tk.LEFT)
        cb = ttk.Combobox(bar, textvariable=self.mode_var, state="readonly", width=26,
                          values=[label for label, _k in _SCF_MODES])
        cb.pack(side=tk.LEFT, padx=6)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._redraw())

    def _mode_key(self):
        return dict(_SCF_MODES).get(self.mode_var.get(), "absolute")

    def add_summary(self):
        n_ct = len(self._calctypes)
        ttk.Label(self, text="{} molecule(s), {} calculation type(s): {}. Hover a bar "
                  "for its structure + energy.".format(
                      len(self._groups), n_ct, ", ".join(self._calctypes)),
                  foreground="#555").pack(side=tk.TOP, anchor=tk.W, padx=10)

    def plot(self, ax):
        self._bar_recs = self._figures.draw_scf_bars(
            ax, self._groups, self._calctypes, mode=self._mode_key(), annotate=False)

    def _bar_at(self, event):
        if event.xdata is None or event.ydata is None:
            return None
        for rec in self._bar_recs:
            r = rec["rect"]
            x0 = r.get_x()
            x1 = x0 + r.get_width()
            h = r.get_height()
            lo, hi = min(0.0, h), max(0.0, h)
            pad = (hi - lo) * 0.03 or 1e-9
            if x0 <= event.xdata <= x1 and (lo - pad) <= event.ydata <= (hi + pad):
                return rec
        return None

    def _hover(self, event):
        ax = self.ax
        if ax is None:
            return
        rec = self._bar_at(event) if event.inaxes is ax else None
        self._clear_hover()
        if rec is None:
            self._set_active(None)
            self.canvas.draw_idle()
            return
        self._set_active(self._mol_index.get(rec["molecule"]))
        self._hover_artists.append(ax.annotate(
            "{}\n{}  {}\nE = {:.6f} Eh".format(rec["name"], rec["molecule"],
                                               rec["calctype"], rec["energy"]),
            xy=(0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
            fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        self.canvas.draw_idle()
