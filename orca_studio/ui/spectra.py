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

from orca_studio.core import spectra as S
from orca_studio.ui.depict import render_smiles_png
from orca_studio.ui.modal import make_modal


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


# --------------------------------------------------------------------------- IR

class IRSpectrumWindow(tk.Toplevel):
    def __init__(self, parent, title, centers, intensities, freqs=None, smiles=None):
        # type: (tk.Misc, str, List[float], List[float], Optional[List[float]], Optional[str]) -> None
        super().__init__(parent)
        self.title("IR spectrum — {}".format(title))
        self.geometry("1000x680")
        self._mol_arr = None      # cached structure image (rendered once)
        self._resize_after = None
        try:
            Figure, FigureCanvasTkAgg = _load_mpl()
        except Exception as e:
            self.destroy()
            _mpl_unavailable_window(parent, e)
            return

        pairs = [(f, i) for f, i in zip(centers, intensities) if abs(f) > 1.0]
        if not pairs:
            ttk.Label(self, text="No vibrational modes with IR intensity found.").pack(padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
            make_modal(self, parent)
            return
        self._centers = [p[0] for p in pairs]
        self._intens = [p[1] for p in pairs]
        self._smiles = smiles

        # Imaginary-mode summary from the full frequency list (if provided).
        all_freqs = freqs if freqs is not None else centers
        real_vibs = [f for f in all_freqs if abs(f) > 1.0]
        n_imag = sum(1 for f in real_vibs if f < 0)
        if n_imag == 0:
            summary = "All {} vibrational frequencies ≥ 0 — a genuine minimum.".format(len(real_vibs))
            summary_fg = "#1a7a1a"
        else:
            imag = sorted(f for f in real_vibs if f < 0)
            summary = ("{} imaginary frequency(ies) < 0: {} — NOT a minimum "
                       "(transition state / saddle point).".format(
                           n_imag, ", ".join("{:.1f}".format(f) for f in imag)))
            summary_fg = "#b00000"

        # ---- controls ----
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(bar, text="FWHM (cm⁻¹):").pack(side=tk.LEFT)
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
        ttk.Button(bar, text="Save image...", command=self._save_image).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        ttk.Label(self, text=summary, foreground=summary_fg).pack(
            side=tk.TOP, anchor=tk.W, padx=10, pady=(4, 0))

        self.fig = Figure(figsize=(8.6, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._peak_disp = []      # (display_x, center, intensity) for hover
        self._hover_artists = []
        self._ymax = 1.0
        self._imax = max(self._intens) or 1.0
        # Populate after the window is realised; matplotlib's own resize handler
        # then keeps the figure fitted to the window (device ratio pinned above).
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
        lo, hi = S.auto_range(self._centers, min_pad=80.0)
        xs, ys = S.broaden(self._centers, self._intens, lo, hi, n=1500, fwhm=fwhm)
        self._ymax = max(ys) if ys else 1.0
        transmission = self.mode_var.get() == "transmission"

        self.fig.clear()
        self._hover_artists = []
        ax = self.fig.add_subplot(111)
        self.ax = ax
        if transmission:
            ymax = self._ymax or 1.0
            tvals = [100.0 * (1.0 - y / ymax) for y in ys]
            ax.plot(xs, tvals, color="tab:green", lw=0.7)
            ax.set_ylim(max(0.0, min(tvals) - 5.0), 102.0)
            ax.set_ylabel("transmittance (%)")
        else:
            ax.plot(xs, ys, color="tab:green", lw=0.7)
            ax.set_ylim(0, self._ymax * 1.12)
            ax.set_ylabel("IR absorbance (a.u.)")

        if self.sticks_var.get():
            for c, it in zip(self._centers, self._intens):
                y0, y1 = self._stick_span(it / self._imax)
                ax.vlines(c, y0, y1, color=(0.05, 0.05, 0.05), linewidth=0.5)

        ax.set_xlim(hi, lo)  # IR convention: high wavenumber on the left
        ax.set_xlabel("wavenumber (cm⁻¹)")
        ax.set_title("Simulated IR spectrum")

        # Structure pinned over the ~2000 cm⁻¹ window — usually free of strong
        # bands (between the X–H stretches and the fingerprint region), so it
        # rarely covers a peak. Anchored top-centre on that wavenumber.
        if self._mol_arr is None:
            self._mol_arr = _smiles_to_array(self._smiles, size=(260, 200))
        if hi != lo:
            frac = (hi - 2000.0) / (hi - lo)        # 0 = left (hi), 1 = right (lo)
            frac = min(0.82, max(0.18, frac))
        else:
            frac = 0.5
        _place_structure_in_axes(ax, self._mol_arr, anchor=(frac, 0.98),
                                 box_align=(0.5, 1.0), target_h=55)

        self.canvas.draw()
        self._cache_peaks()

    def _cache_peaks(self):
        # Kept for back-compat; hover now works in data coordinates (below), so
        # it's robust to HiDPI device-pixel scaling.
        self._peak_disp = [(c, c, it) for c, it in zip(self._centers, self._intens)]

    def _clear_hover(self):
        for a in self._hover_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._hover_artists = []

    def _on_motion(self, event):
        if event.inaxes is not self.ax or event.xdata is None:
            if self._hover_artists:
                self._clear_hover(); self.canvas.draw_idle()
            return
        # Grab the peak(s) right under the pointer, comparing in data units
        # (cm⁻¹) so it's independent of the display's pixel scaling.
        x0, x1 = self.ax.get_xlim()
        tol = abs(x1 - x0) * 0.01 or 5.0
        near = [(c, it) for c, it in zip(self._centers, self._intens)
                if abs(c - event.xdata) <= tol]
        self._clear_hover()
        if not near:
            self.canvas.draw_idle()
            return
        # Temporarily show the sticks for these peaks (even if the checkbox is off).
        for c, it in near:
            y0, y1 = self._stick_span(it / self._imax)
            self._hover_artists.append(
                self.ax.vlines(c, y0, y1, color=(0.05, 0.05, 0.05), linewidth=0.9))
        # List the peaks high→low wavenumber with intensities.
        near_sorted = sorted(near, key=lambda t: t[0], reverse=True)
        text = "\n".join("{:.1f} cm⁻¹   I={:.1f}".format(c, it) for c, it in near_sorted)
        self._hover_artists.append(self.ax.annotate(
            text, xy=(0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
            fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="#fffbe6", ec="#888")))
        self.canvas.draw_idle()

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
                # short label for the cramped thumbnail column (molecule part only)
                "short": name.split(" / ")[0][:14],
                "color": _COLORS[idx % len(_COLORS)],
                "shifts": shifts,
                "img": _smiles_to_array(e.get("smiles"), size=(300, 230)),
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
        ttk.Button(bar, text="Save image...", command=self._save_image).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        self.fig = Figure(figsize=(11.0, 6.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._thumb_axes = []
        self._annot = None
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
        return S.auto_range(all_shifts, pad_frac=0.12, min_pad=1.0)

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
        n_mol = len(self.mols)
        # Thumbnails get a narrow column (~11 % of the width) so the structures
        # stay small and the spectrum keeps the room.
        gs = self.fig.add_gridspec(max(1, n_mol), 2, width_ratios=[8, 1])
        self.ax = self.fig.add_subplot(gs[:, 0])
        self._thumb_axes = []

        ymax = 0.0
        for m in self.mols:
            xs, ys = S.broaden(m["shifts"], None, grid_lo, grid_hi, n=1600, fwhm=fwhm)
            self.ax.plot(xs, ys, color=m["color"], lw=1.4)
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
        # No legend — the colour-matched structure thumbnails identify molecules
        # and it otherwise overlaps the leftmost peaks.

        for mi, m in enumerate(self.mols):
            tax = self.fig.add_subplot(gs[mi, 1])
            if m["img"] is not None:
                tax.imshow(m["img"])
            else:
                tax.text(0.5, 0.5, "(no structure)", ha="center", va="center", fontsize=7)
            tax.set_xticks([]); tax.set_yticks([])
            tax.set_title(m["short"], fontsize=7, color=m["color"])
            for sp in tax.spines.values():
                sp.set_edgecolor(m["color"]); sp.set_linewidth(1.0)
            self._thumb_axes.append(tax)

        self.canvas.draw()
        # reflect the active x-range in the entry boxes
        x0, x1 = self.ax.get_xlim()
        if self._user_xlim is None:
            self.xmin_var.set("{:.2f}".format(min(x0, x1)))
            self.xmax_var.set("{:.2f}".format(max(x0, x1)))

    def _on_motion(self, event):
        if event.inaxes is not self.ax or event.xdata is None:
            self._set_active(None)
            return
        # Work in data units (ppm) so it's symmetric about each peak and
        # independent of the display's pixel scaling.
        x0, x1 = self.ax.get_xlim()
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
        for j, tax in enumerate(self._thumb_axes):
            active = (j == mi)
            for sp in tax.spines.values():
                sp.set_linewidth(3.0 if active else 1.0)
            tax.set_title(self.mols[j]["short"], fontsize=(8 if active else 7),
                          fontweight=("bold" if active else "normal"),
                          color=self.mols[j]["color"])
        if self._annot is not None:
            try:
                self._annot.remove()
            except Exception:
                pass
            self._annot = None
        if mi is not None:
            m = self.mols[mi]
            self._annot = self.ax.annotate(
                m["name"], xy=(0.02, 0.95), xycoords="axes fraction",
                fontsize=11, fontweight="bold", color=m["color"],
                bbox=dict(boxstyle="round", fc="white", ec=m["color"]))
        self.canvas.draw_idle()

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
