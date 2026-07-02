"""Simulated IR and NMR spectra windows (matplotlib embedded in Tk).

IR: broadened spectrum vs wavenumber for one FREQ calc. Toggle absorbance vs
transmission, optional stick lines, hover to read off the peaks near the cursor,
a cropped structure image pinned to the top-right, and an imaginary-mode summary.

NMR: overlaid simulated spectra for one or more NMR calcs of a chosen isotope,
converted to chemical shift via a user-supplied reference shielding. A panel of
colour-matched structure thumbnails sits beside the spectrum; hovering a peak
highlights the molecule it belongs to. Adjustable x-range and image export.

matplotlib is a soft dependency (already needed for live plots); structure
images additionally need matplotlib's PNG reader (Pillow). Everything degrades
gracefully if those are missing.
"""

import io
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from orca_workbench.core import spectra as S
from orca_workbench.core import epr as EPR_sim
from orca_workbench.ui.depict import render_smiles_png
from orca_workbench.ui.modal import make_modal


def _load_mpl():
    """Return the matplotlib pieces or raise with a friendly message."""
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    return Figure, FigureCanvasTkAgg


def pin_device_pixel_ratio(canvas):
    """Stop matplotlib from sizing the figure by the global Tk 'scaling' factor.

    On HiDPI Windows, matplotlib derives the figure's device-pixel-ratio from
    `tk scaling` (which we set larger for readable fonts), so the figure renders
    ~1.2–1.5× too big and overflows the window — and a recompute on the next
    <Configure> makes it grow a moment after opening. Pinning the ratio to 1.0
    means the figure size is driven purely by the Tk widget size (matplotlib's
    own resize handler fits it), independent of the UI font scaling.

    We override `_set_device_pixel_ratio` to always apply 1.0: matplotlib's
    <Configure> handler still recomputes the ratio from `tk scaling` and calls
    this, but the override forces it back to 1.0 (reassigning the recompute
    method itself wouldn't help — its binding captured the original)."""
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


def _place_structure_in_axes(ax, arr, anchor=(0.985, 0.985), box_align=(1.0, 1.0),
                             target_h=110):
    """Pin a structure image *inside* the plot axes at a fixed display height,
    undistorted and clipped to the axes so it can't spill out. `anchor` is the
    (x, y) position in axes fraction and `box_align` how the image box aligns to
    it (e.g. (0.5, 1.0) = centred horizontally, top edge on the anchor)."""
    if arr is None:
        return None
    try:
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox
    except Exception:
        return None
    zoom = float(target_h) / float(arr.shape[0])
    oi = OffsetImage(arr, zoom=zoom)
    ab = AnnotationBbox(oi, anchor, xycoords="axes fraction", box_alignment=box_align,
                        frameon=False, pad=0.0, zorder=5)
    ax.add_artist(ab)
    try:
        ab.set_clip_on(True)
    except Exception:
        pass
    return ab


# ----------------------------------------------------------- shared UI helpers

class _AxisLimitControls(object):
    """A tiny toolbar group of x0/x1/y0/y1 entry fields (blank = auto). Every plot
    window creates one and calls `apply(ax)` at the end of its redraw, so any axis
    limit can be pinned to an explicit data value while the rest stay auto-scaled."""

    def __init__(self, bar, redraw_cb):
        self.vars = {}
        ttk.Label(bar, text="x:").pack(side=tk.LEFT, padx=(8, 0))
        for k in ("x0", "x1"):
            self.vars[k] = tk.StringVar()
            e = ttk.Entry(bar, textvariable=self.vars[k], width=6)
            e.pack(side=tk.LEFT, padx=1)
            e.bind("<Return>", lambda _e: redraw_cb())
        ttk.Label(bar, text="y:").pack(side=tk.LEFT, padx=(6, 0))
        for k in ("y0", "y1"):
            self.vars[k] = tk.StringVar()
            e = ttk.Entry(bar, textvariable=self.vars[k], width=6)
            e.pack(side=tk.LEFT, padx=1)
            e.bind("<Return>", lambda _e: redraw_cb())
        ttk.Button(bar, text="Set", width=4, command=redraw_cb).pack(side=tk.LEFT, padx=(2, 4))

    def _f(self, k):
        try:
            return float(self.vars[k].get())
        except (ValueError, TypeError):
            return None

    def apply(self, ax):
        x0, x1 = self._f("x0"), self._f("x1")
        y0, y1 = self._f("y0"), self._f("y1")
        if x0 is not None or x1 is not None:
            cur = ax.get_xlim()
            ax.set_xlim(x0 if x0 is not None else cur[0], x1 if x1 is not None else cur[1])
        if y0 is not None or y1 is not None:
            cur = ax.get_ylim()
            ax.set_ylim(y0 if y0 is not None else cur[0], y1 if y1 is not None else cur[1])


def _maximize_window(win):
    """Best-effort maximise a Toplevel across platforms / window managers."""
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
    updated on hover. Replaces per-trace thumbnails: exactly one structure is on
    screen at a time — whichever trace the cursor is over."""

    def __init__(self, parent, width=300):
        ttk.Frame.__init__(self, parent, width=width)
        self.pack_propagate(False)
        self.name = ttk.Label(self, text="hover a peak", anchor="center",
                              font=("TkDefaultFont", 10, "bold"))
        self.name.pack(side=tk.TOP, fill=tk.X, pady=(10, 4), padx=6)
        self.img = ttk.Label(self, anchor="center")
        self.img.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._cache = {}     # smiles -> PhotoImage (or None)
        self._cur = object()  # sentinel so the first show() always renders

    def show(self, key, name, smiles, color="#000000"):
        if key == self._cur:
            return
        self._cur = key
        self.name.configure(text=name or "", foreground=color or "#000000")
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
        self.img.configure(image="")
        self.img.image = None


# --------------------------------------------------------------------------- IR

class IRSpectrumWindow(tk.Toplevel):
    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        # entries: [{name, smiles, centers, intensities, freqs}] — one or more,
        # stacked as colour-matched traces.
        super().__init__(parent)
        self.title("IR spectrum — {}".format(title))
        self.geometry("1100x700")
        try:
            Figure, FigureCanvasTkAgg = _load_mpl()
        except Exception as e:
            self.destroy()
            _mpl_unavailable_window(parent, e)
            return

        self.mols = []
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
        self._stacked = len(self.mols) > 1
        self._active = None
        self._hover_artists = []
        self._ymax = 1.0

        if not self.mols:
            ttk.Label(self, text="No vibrational modes with IR intensity found.").pack(padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
            make_modal(self, parent)
            return

        # Imaginary-mode summary, combined across molecules.
        if sum(len(m["imag"]) for m in self.mols) == 0:
            summary = "All vibrational frequencies >= 0 - genuine minima."
            summary_fg = "#1a7a1a"
        else:
            bits = ["{}: {}".format(m["short"], ", ".join("{:.1f}".format(f) for f in m["imag"]))
                    for m in self.mols if m["imag"]]
            summary = "Imaginary frequency(ies) - NOT a minimum: " + "; ".join(bits)
            summary_fg = "#b00000"

        # ---- controls ----
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(bar, text="FWHM (cm^-1):").pack(side=tk.LEFT)
        self.fwhm_var = tk.DoubleVar(value=20.0)
        sp = ttk.Spinbox(bar, from_=2, to=80, increment=2, width=6, textvariable=self.fwhm_var,
                         command=self._redraw)
        sp.pack(side=tk.LEFT, padx=6)
        sp.bind("<Return>", lambda e: self._redraw())          # Enter confirms the typed value
        self.sticks_var = tk.BooleanVar(value=False)            # sticks OFF by default
        ttk.Checkbutton(bar, text="Stick lines", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)
        self.mode_var = tk.StringVar(value="absorbance")
        self.mode_btn = ttk.Button(bar, text="Show: Absorbance", command=self._toggle_mode)
        self.mode_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Redraw", command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Maximize", command=lambda: _maximize_window(self)).pack(side=tk.RIGHT, padx=2)
        self._axlim = _AxisLimitControls(bar, self._redraw)
        ttk.Button(bar, text="Save image...", command=self._save_image).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        ttk.Label(self, text=summary, foreground=summary_fg).pack(
            side=tk.TOP, anchor=tk.W, padx=10, pady=(4, 0))

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.struct = _StructurePanel(body, width=300)
        self.struct.pack(side=tk.RIGHT, fill=tk.Y)
        self.fig = Figure(figsize=(8.6, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=body)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.after(0, self._first_draw)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        make_modal(self, parent)

    def _first_draw(self):
        self.update_idletasks()
        self._redraw()

    def _toggle_mode(self):
        self.mode_var.set("transmission" if self.mode_var.get() == "absorbance" else "absorbance")
        self.mode_btn.configure(text="Show: " + self.mode_var.get().capitalize())
        self._redraw()

    def _stick_span(self, frac):
        """(y0, y1) for a stick of relative height frac in the current mode."""
        if self.mode_var.get() == "transmission":
            return (100.0, 100.0 * (1.0 - frac))
        return (0.0, self._ymax * frac)

    def _redraw(self):
        fwhm = max(1.0, float(self.fwhm_var.get()))
        all_centers = [c for m in self.mols for c in m["centers"]]
        lo, hi = S.auto_range(all_centers, min_pad=80.0)
        transmission = self.mode_var.get() == "transmission"

        self.fig.clear()
        self._hover_artists = []
        ax = self.fig.add_subplot(111)
        self.ax = ax

        ymax = 0.0
        for m in self.mols:
            xs, ys = S.broaden(m["centers"], m["intens"], lo, hi, n=1500, fwhm=fwhm)
            m["_imax"] = max(m["intens"]) or 1.0
            if transmission:
                ypk = max(ys) or 1.0
                tvals = [100.0 * (1.0 - y / ypk) for y in ys]
                ax.plot(xs, tvals, color=m["color"], lw=0.8, label=m["short"])
            else:
                ax.plot(xs, ys, color=m["color"], lw=0.8, label=m["short"])
                ymax = max(ymax, max(ys) if ys else 0.0)
        if transmission:
            ax.set_ylim(-2.0, 102.0)
            ax.set_ylabel("transmittance (%)")
        else:
            self._ymax = ymax or 1.0
            ax.set_ylim(0, self._ymax * 1.12)
            ax.set_ylabel("IR absorbance (a.u.)")

        if self.sticks_var.get():
            for m in self.mols:
                for c, it in zip(m["centers"], m["intens"]):
                    y0, y1 = self._stick_span(it / m["_imax"])
                    ax.vlines(c, y0, y1, color=m["color"], linewidth=0.5, alpha=0.6)

        ax.set_xlim(hi, lo)  # IR convention: high wavenumber on the left
        ax.set_xlabel(r"wavenumber (cm$^{-1}$)")
        ax.set_title("Simulated IR spectrum")
        if self._stacked:
            ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        else:
            m = self.mols[0]
            self.struct.show(0, m["name"], m.get("smiles"), m["color"])

        if getattr(self, "_axlim", None):
            self._axlim.apply(self.fig.gca())
        self.canvas.draw()

    def _clear_hover(self):
        for a in self._hover_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._hover_artists = []

    def _on_motion(self, event):
        ax = getattr(self, "ax", None)
        if ax is None:
            return  # first draw hasn't run yet (deferred); nothing to hover
        if event.inaxes is not ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover(); self.canvas.draw_idle()
            if self._stacked:
                self._set_active(None)
            return
        x0, x1 = ax.get_xlim()
        tol = abs(x1 - x0) * 0.01 or 5.0
        # nearest peak across molecules -> which molecule the cursor is over
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
        # On-hover sticks only make sense for a single spectrum; in a stacked
        # view they'd be ambiguous across molecules, so skip them there.
        if not self._stacked:
            for c, it in near:
                y0, y1 = self._stick_span(it / m["_imax"])
                self._hover_artists.append(
                    ax.vlines(c, y0, y1, color=(0.05, 0.05, 0.05), linewidth=0.9))
        near_sorted = sorted(near, key=lambda t: t[0], reverse=True)
        text = "\n".join("{:.1f}   I={:.1f}".format(c, it) for c, it in near_sorted)
        self._hover_artists.append(ax.annotate(
            text, xy=(0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
            fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        if self._stacked:
            self._set_active(best)
        self.canvas.draw_idle()

    def _set_active(self, mi):
        if mi == self._active:
            return
        self._active = mi
        if mi is None:
            self.struct.clear()
        else:
            m = self.mols[mi]
            self.struct.show(mi, m["name"], m.get("smiles"), m["color"])

    def _save_image(self):
        _save_figure(self.fig, self)


# ------------------------------------------------------------------------ UV-Vis

_EV_NM = 1239.841984   # eV*nm: E[eV] = _EV_NM / lambda[nm]


class UVVisSpectrumWindow(tk.Toplevel):
    """Simulated UV-Vis absorption from one or more TD-DFT calcs: oscillator-
    strength sticks Gaussian-broadened into bands, x-axis in nm (default) or eV,
    several molecules stacked as colour-matched traces with a hover structure panel."""

    def __init__(self, parent, title, entries):
        # type: (tk.Misc, str, List[dict]) -> None
        # entries: [{name, smiles, states:[{wavelength_nm, energy_eV, fosc}]}]
        super().__init__(parent)
        self.title("UV-Vis spectrum - {}".format(title))
        self.geometry("1100x700")
        try:
            Figure, FigureCanvasTkAgg = _load_mpl()
        except Exception as e:
            self.destroy()
            _mpl_unavailable_window(parent, e)
            return

        self.mols = []
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
        self._stacked = len(self.mols) > 1
        self._active = None
        self._hover_artists = []
        self._ymax = 1.0

        if not self.mols:
            ttk.Label(self, text="No excited states found in the selected calculation(s).").pack(
                padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
            make_modal(self, parent)
            return

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        self.axis_var = tk.StringVar(value="nm")
        ttk.Label(bar, text="x-axis:").pack(side=tk.LEFT)
        self.axis_btn = ttk.Button(bar, text="Wavelength (nm)", command=self._toggle_axis)
        self.axis_btn.pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text="FWHM:").pack(side=tk.LEFT, padx=(10, 0))
        self.fwhm_var = tk.DoubleVar(value=20.0)
        sp = ttk.Spinbox(bar, from_=1, to=200, increment=1, width=6, textvariable=self.fwhm_var,
                         command=self._redraw)
        sp.pack(side=tk.LEFT, padx=6)
        sp.bind("<Return>", lambda e: self._redraw())
        self.sticks_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Stick lines", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)
        ttk.Button(bar, text="Redraw", command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Maximize", command=lambda: _maximize_window(self)).pack(side=tk.RIGHT, padx=2)
        self._axlim = _AxisLimitControls(bar, self._redraw)
        ttk.Button(bar, text="Save image...", command=self._save_image).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.struct = _StructurePanel(body, width=300)
        self.struct.pack(side=tk.RIGHT, fill=tk.Y)
        self.fig = Figure(figsize=(8.6, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=body)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.after(0, self._first_draw)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        make_modal(self, parent)

    def _first_draw(self):
        self.update_idletasks()
        self._redraw()

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

    def _redraw(self):
        fwhm = max(0.001, float(self.fwhm_var.get()))
        nm_axis = self.axis_var.get() == "nm"
        all_centers = [c for m in self.mols for c in self._centers(m)]
        lo, hi = S.auto_range(all_centers, min_pad=(30.0 if nm_axis else 0.5))
        if nm_axis:
            lo = max(0.0, lo)

        self.fig.clear()
        self._hover_artists = []
        ax = self.fig.add_subplot(111)
        self.ax = ax

        ymax = 0.0
        for m in self.mols:
            xs, ys = S.broaden(self._centers(m), m["fosc"], lo, hi, n=1400, fwhm=fwhm,
                               shape="gaussian")
            ax.plot(xs, ys, color=m["color"], lw=1.0, label=m["short"])
            ymax = max(ymax, max(ys) if ys else 0.0)
            m["_fmax"] = max(m["fosc"]) or 1.0
        self._ymax = ymax or 1.0
        ax.set_ylim(0, self._ymax * 1.12)

        if self.sticks_var.get():
            for m in self.mols:
                for c, f in zip(self._centers(m), m["fosc"]):
                    if f <= 0:
                        continue
                    ax.vlines(c, 0.0, self._ymax * (f / m["_fmax"]),
                              color=m["color"], linewidth=0.6, alpha=0.6)

        ax.set_xlim(lo, hi)
        ax.set_xlabel("wavelength (nm)" if nm_axis else "energy (eV)")
        ax.set_ylabel("oscillator strength / absorbance (a.u.)")
        ax.set_title("Simulated UV-Vis spectrum")
        if self._stacked:
            ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        else:
            m = self.mols[0]
            self.struct.show(0, m["name"], m.get("smiles"), m["color"])
        if getattr(self, "_axlim", None):
            self._axlim.apply(self.fig.gca())
        self.canvas.draw()

    def _clear_hover(self):
        for a in self._hover_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._hover_artists = []

    def _on_motion(self, event):
        ax = getattr(self, "ax", None)
        if ax is None:
            return
        if event.inaxes is not ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover()
                self.canvas.draw_idle()
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
            for c, f in near:
                if f <= 0:
                    continue
                self._hover_artists.append(
                    ax.vlines(c, 0.0, self._ymax * (f / m["_fmax"]),
                              color=(0.05, 0.05, 0.05), linewidth=0.9))
        text = "\n".join("{:.1f} {}   f={:.3f}".format(c, unit, f)
                         for c, f in sorted(near, key=lambda t: t[0]))
        if text:
            self._hover_artists.append(ax.annotate(
                text, xy=(0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
                fontsize=9, family="monospace",
                bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        if self._stacked:
            self._set_active(best)
        self.canvas.draw_idle()

    def _set_active(self, mi):
        if mi == self._active:
            return
        self._active = mi
        if mi is None:
            self.struct.clear()
        else:
            m = self.mols[mi]
            self.struct.show(mi, m["name"], m.get("smiles"), m["color"])

    def _save_image(self):
        _save_figure(self.fig, self)


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


# A consistent qualitative colour cycle for molecules.
_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
           "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


class NMRSpectrumWindow(tk.Toplevel):
    """entries: list of dicts with keys name, smiles, shieldings (floats for
    the chosen element). nucleus_label e.g. '1H' is for display; reference sets
    the axis (δ vs raw σ)."""

    def __init__(self, parent, entries, nucleus_label, reference):
        super().__init__(parent)
        self.title("NMR spectrum — {}".format(nucleus_label))
        self.geometry("1200x760")
        self._resize_after = None
        try:
            Figure, FigureCanvasTkAgg = _load_mpl()
        except Exception as e:
            self.destroy()
            _mpl_unavailable_window(parent, e)
            return

        self.mols = []
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
        self._user_xlim = None

        if not self.mols:
            ttk.Label(self, text="No {} nuclei found in the selected calculations."
                      .format(nucleus_label)).pack(padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
            make_modal(self, parent)
            return

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(bar, text="FWHM (ppm):").pack(side=tk.LEFT)
        self.fwhm_var = tk.DoubleVar(value=0.2)
        sp = ttk.Spinbox(bar, from_=0.02, to=5.0, increment=0.05, width=6, textvariable=self.fwhm_var,
                         command=self._redraw)
        sp.pack(side=tk.LEFT, padx=6)
        sp.bind("<Return>", lambda e: self._redraw())
        ttk.Label(bar, text="x-range:").pack(side=tk.LEFT, padx=(12, 2))
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        e1 = ttk.Entry(bar, textvariable=self.xmin_var, width=7); e1.pack(side=tk.LEFT)
        ttk.Label(bar, text="to").pack(side=tk.LEFT, padx=2)
        e2 = ttk.Entry(bar, textvariable=self.xmax_var, width=7); e2.pack(side=tk.LEFT)
        for e in (e1, e2):
            e.bind("<Return>", lambda ev: self._apply_xrange())
        ttk.Button(bar, text="Apply", command=self._apply_xrange).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Auto", command=self._auto_xrange).pack(side=tk.LEFT)
        ttk.Label(bar, text="(hover a peak to highlight its molecule)",
                  foreground="#666").pack(side=tk.LEFT, padx=12)
        ttk.Button(bar, text="Maximize", command=lambda: _maximize_window(self)).pack(side=tk.RIGHT, padx=2)
        self._axlim = _AxisLimitControls(bar, self._redraw)
        ttk.Button(bar, text="Save image...", command=self._save_image).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.struct = _StructurePanel(body, width=300)
        self.struct.pack(side=tk.RIGHT, fill=tk.Y)
        self.fig = Figure(figsize=(9.0, 6.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=body)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._active = None
        # Populate once realised; matplotlib's resize keeps it fitted (device
        # ratio pinned above).
        self.after(0, self._first_draw)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        make_modal(self, parent)

    def _first_draw(self):
        self.update_idletasks()
        self._redraw()

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

    def _redraw(self):
        fwhm = max(0.01, float(self.fwhm_var.get()))
        # Determine the view range first (user override, else auto), and broaden
        # the curves over that whole range (plus a margin) so they fill the axis
        # rather than stopping at the data's auto-range edges.
        if self._user_xlim is not None:
            view_lo, view_hi = min(self._user_xlim), max(self._user_xlim)
        else:
            view_lo, view_hi = self._auto_range_vals()
        margin = (view_hi - view_lo) * 0.05 or 0.5
        grid_lo, grid_hi = view_lo - margin, view_hi + margin

        self.fig.clear()
        self.ax = self.fig.add_subplot(111)

        ymax = 0.0
        for m in self.mols:
            xs, ys = S.broaden(m["shifts"], None, grid_lo, grid_hi, n=1600, fwhm=fwhm)
            self.ax.plot(xs, ys, color=m["color"], lw=1.4, label=m["short"])
            if ys:
                ymax = max(ymax, max(ys))

        xlabel = ("chemical shift δ (ppm)" if self.reference is not None
                  else "isotropic shielding σ (ppm)")
        # NMR convention: high shift / low shielding on the left (axis reversed).
        self.ax.set_xlim(view_hi, view_lo)
        self.ax.set_ylim(0, (ymax or 1.0) * 1.12)   # fit the tallest peak
        self.ax.set_xlabel(xlabel)
        self.ax.set_ylabel("intensity (a.u.)")
        self.ax.set_title("Simulated {} NMR".format(self.nucleus_label))
        # Structures are no longer in the axes (one shared panel shows the hovered
        # trace), so a compact legend can identify the traces without crowding.
        if len(self.mols) > 1:
            self.ax.legend(loc="upper right", fontsize=8, framealpha=0.9)

        self._active = None
        self.struct.clear()
        if getattr(self, "_axlim", None):
            self._axlim.apply(self.fig.gca())
        self.canvas.draw()
        # reflect the active x-range in the entry boxes
        x0, x1 = self.ax.get_xlim()
        if self._user_xlim is None:
            self.xmin_var.set("{:.2f}".format(min(x0, x1)))
            self.xmax_var.set("{:.2f}".format(max(x0, x1)))

    def _on_motion(self, event):
        ax = getattr(self, "ax", None)
        if ax is None:
            return  # first draw hasn't run yet (deferred); nothing to hover
        if event.inaxes is not ax or event.xdata is None:
            self._set_active(None)
            return
        # Work in data units (ppm) so it's symmetric about each peak and
        # independent of the display's pixel scaling.
        x0, x1 = ax.get_xlim()
        tol = abs(x1 - x0) * 0.025 or 0.5
        best = None
        best_d = 1e9
        for mi, m in enumerate(self.mols):
            for sh in m["shifts"]:
                d = abs(sh - event.xdata)
                if d < best_d:
                    best_d = d
                    best = mi
        self._set_active(best if best_d <= tol else None)

    def _set_active(self, mi):
        if mi == self._active:
            return
        self._active = mi
        if mi is None:
            self.struct.clear()
        else:
            m = self.mols[mi]
            self.struct.show(mi, m["name"], m.get("smiles"), m["color"])

    def _save_image(self):
        _save_figure(self.fig, self)


_KNOWN_IMG_EXT = {".png", ".pdf", ".svg", ".svgz", ".jpg", ".jpeg",
                  ".tif", ".tiff", ".eps", ".ps"}


def _ask_image_format(parent):
    # type: (tk.Misc) -> Optional[str]
    """Tiny modal asking which image format to use (when the typed filename had
    no extension). Returns 'png'/'svg'/... or None."""
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
    # No defaultextension: hardcoding it made the non-native Tk dialog append
    # ".png" even when another filter was picked. Instead we honour whatever
    # extension the user typed, and only ask for a format if none was given.
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


class EPRSpectrumWindow(tk.Toplevel):
    """Simulated isotropic (solution) EPR spectrum from a finished %eprnmr calc: the
    first-derivative lineshape swept in magnetic field at a fixed microwave
    frequency, built from g_iso + the isotropic hyperfine couplings. Annotates the
    g-value, centre field and equivalent-coupling groups; shows the structure panel.
    (Isotropic / spin-1/2 / 100%-abundance model — see core.epr.)"""

    def __init__(self, parent, title, epr, name=None, smiles=None):
        # type: (tk.Misc, str, dict, Optional[str], Optional[str]) -> None
        super().__init__(parent)
        self.title("EPR spectrum - {}".format(title))
        self.geometry("1100x700")
        try:
            Figure, FigureCanvasTkAgg = _load_mpl()
        except Exception as e:
            self.destroy()
            _mpl_unavailable_window(parent, e)
            return
        self.epr = epr or {}
        self.g_iso = (self.epr.get("g_tensor") or {}).get("g_iso")
        self.hyperfine = self.epr.get("hyperfine") or []
        self._name = name or title
        self._smiles = smiles

        if self.g_iso is None:
            ttk.Label(self, text="No g-tensor found in the calculation.").pack(padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
            make_modal(self, parent)
            return

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(bar, text="MW freq (GHz):").pack(side=tk.LEFT)
        self.freq_var = tk.DoubleVar(value=9.5)
        sp1 = ttk.Spinbox(bar, from_=1, to=400, increment=0.5, width=7,
                          textvariable=self.freq_var, command=self._redraw)
        sp1.pack(side=tk.LEFT, padx=6)
        sp1.bind("<Return>", lambda e: self._redraw())
        self.band_var = tk.StringVar(value="X (9.5 GHz)")
        band_cb = ttk.Combobox(bar, textvariable=self.band_var, width=12, state="readonly",
                               values=["{} ({:g} GHz)".format(nm, gh) for nm, gh in _EPR_BANDS])
        band_cb.pack(side=tk.LEFT, padx=(2, 0))
        band_cb.bind("<<ComboboxSelected>>", self._on_band)
        ttk.Label(bar, text="Linewidth (mT):").pack(side=tk.LEFT, padx=(10, 0))
        self.lw_var = tk.DoubleVar(value=0.15)
        sp2 = ttk.Spinbox(bar, from_=0.01, to=10, increment=0.05, width=7,
                          textvariable=self.lw_var, command=self._redraw)
        sp2.pack(side=tk.LEFT, padx=6)
        sp2.bind("<Return>", lambda e: self._redraw())
        self.mode = "iso"
        self.mode_btn = ttk.Button(bar, text="Mode: isotropic", width=22,
                                   command=self._toggle_mode)
        self.mode_btn.pack(side=tk.LEFT, padx=(10, 2))
        ttk.Label(bar, text="Show:").pack(side=tk.LEFT, padx=(10, 0))
        self.disp_var = tk.StringVar(value="1st derivative")
        disp_cb = ttk.Combobox(bar, textvariable=self.disp_var, width=13, state="readonly",
                               values=["1st derivative", "Absorption", "2nd derivative"])
        disp_cb.pack(side=tk.LEFT, padx=(2, 0))
        disp_cb.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        self.sticks_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Line markers", variable=self.sticks_var,
                        command=self._redraw).pack(side=tk.LEFT, padx=10)
        ttk.Button(bar, text="Redraw", command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Maximize", command=lambda: _maximize_window(self)).pack(side=tk.RIGHT, padx=2)
        self._axlim = _AxisLimitControls(bar, self._redraw)
        ttk.Button(bar, text="Save image...", command=self._save_image).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.struct = _StructurePanel(body, width=300)
        self.struct.pack(side=tk.RIGHT, fill=tk.Y)
        self.fig = Figure(figsize=(8.6, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=body)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.after(0, self._first_draw)
        make_modal(self, parent)

    def _first_draw(self):
        self.update_idletasks()
        self.struct.show(0, self._name, self._smiles, _COLORS[0])
        self._redraw()

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

    def _g_principal(self):
        return (self.epr.get("g_tensor") or {}).get("g") or [self.g_iso] * 3

    def _redraw(self):
        freq = max(0.1, float(self.freq_var.get()))
        lw = max(0.001, float(self.lw_var.get()))
        powder = self.mode == "powder"
        if powder:
            sim = EPR_sim.powder_spectrum(self._g_principal(), self.hyperfine,
                                          freq_GHz=freq, linewidth_mT=lw,
                                          n_theta=40, n_phi=80, npoints=2500)
        else:
            sim = EPR_sim.simulate(self.g_iso, self.hyperfine, freq_GHz=freq, linewidth_mT=lw)
        field = sim["field_mT"]
        disp = self.disp_var.get()
        if disp == "Absorption":
            trace = sim.get("absorption") or sim["derivative"]
            ylabel = "absorption (a.u.)"
        elif disp == "2nd derivative":
            trace = self._derivative(sim["derivative"], field)
            ylabel = "2nd derivative (a.u.)"
        else:
            trace = sim["derivative"]
            ylabel = "first-derivative absorption (a.u.)"
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.plot(field, trace, color=_COLORS[0], lw=1.0)
        ax.axhline(0, color="#cccccc", lw=0.5)
        if self.sticks_var.get() and sim.get("sticks"):
            peak = max((abs(v) for v in trace), default=1.0) or 1.0
            imax = max((i for _, i in sim["sticks"]), default=1.0) or 1.0
            base = 0.0 if disp != "Absorption" else 0.0
            for bc, inten in sim["sticks"]:
                ax.vlines(bc, base, base + peak * (inten / imax), color="#888888",
                          linewidth=0.6, alpha=0.6)
        ax.set_xlabel("magnetic field (mT)")
        ax.set_ylabel(ylabel)
        ax.set_title("Simulated {} EPR spectrum".format(
            "powder (anisotropic)" if powder else "isotropic"))

        def _coupling_mhz(g):
            a = g["A"]
            return abs(sum(a) / 3.0) if isinstance(a, (list, tuple)) else abs(a)

        if powder:
            gp = self._g_principal()
            txt = "g = [{:.5f}, {:.5f}, {:.5f}]\nB0(g_iso) = {:.1f} mT @ {:.2f} GHz".format(
                gp[0], gp[1], gp[2], sim["center_mT"], freq)
        else:
            txt = "g_iso = {:.5f}\nB0 = {:.1f} mT @ {:.2f} GHz".format(
                self.g_iso, sim["center_mT"], freq)
        groups = sim["groups"]
        if groups:
            txt += "\n" + "\n".join(
                "a({}) = {:.1f} MHz  x{}".format(g["element"], _coupling_mhz(g), g["count"])
                for g in groups[:8])
        ax.text(0.01, 0.99, txt, transform=ax.transAxes, va="top", ha="left",
                fontsize=8, family="monospace",
                bbox=dict(boxstyle="round", fc="white", ec="#cccccc", alpha=0.85))
        if getattr(self, "_axlim", None):
            self._axlim.apply(self.fig.gca())
        self.canvas.draw()

    def _save_image(self):
        _save_figure(self.fig, self)
