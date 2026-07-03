"""Live-progress plot window for a running (or finished) ORCA job.

Embeds matplotlib in a Tk Toplevel and re-reads the job's .out file on a timer,
re-plotting the optimisation energy trajectory and the gradient convergence.
Because the app runs on the cluster and the SLURM .out streams to the shared
filesystem (stdbuf wrapper in the template), this just reads a local file — no
network, no SSH.

matplotlib is a soft dependency: if it isn't installed the window explains how
to get it instead of crashing.
"""

import os
import tkinter as tk
from tkinter import ttk

from orca_workbench.core import orca_parser


def _pin_device_pixel_ratio(canvas):
    """Pin matplotlib's device pixel ratio to 1.0 so the figure is sized by the
    Tk widget, not the global tk scaling (which we raise for readable fonts).
    Without this the embedded figure renders too big on HiDPI Windows and
    overflows the window. See spectra.pin_device_pixel_ratio for the details."""
    try:
        orig = canvas._set_device_pixel_ratio
        canvas._set_device_pixel_ratio = lambda ratio, _o=orig: _o(1.0)
        canvas._set_device_pixel_ratio(1.0)
    except Exception:
        pass


class LivePlotWindow(tk.Toplevel):
    def __init__(self, parent, title, out_path, poll_ms=2000):
        super().__init__(parent)
        self.title("Progress — {}".format(title))
        self.geometry("900x720")
        self.out_path = out_path
        self.poll_ms = poll_ms
        self._after_id = None
        self._closed = False
        self._resize_after = None

        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as e:  # ImportError, or backend/display issues
            msg = ("Could not initialise matplotlib:\n  {}\n\n"
                   "Install it on the cluster with:\n"
                   "  pip install --user matplotlib\n\n"
                   "(The rest of ORCA Workbench works without it — you just won't get "
                   "live plots.)".format(e))
            ttk.Label(self, text=msg, justify=tk.LEFT, wraplength=520).pack(
                padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=(0, 12))
            return

        self.status_var = tk.StringVar(value="reading...")
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W).pack(
            side=tk.TOP, fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(self, text=out_path, foreground="#666", anchor=tk.W).pack(
            side=tk.TOP, fill=tk.X, padx=8)

        self.fig = Figure(figsize=(7.2, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.ax_energy = self.fig.add_subplot(211)
        self.ax_conv = self.fig.add_subplot(212)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        _pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="Auto-refresh (every {}s)".format(poll_ms // 1000),
                        variable=self.auto_var).pack(side=tk.LEFT)
        ttk.Button(btns, text="Refresh now", command=self._update_once).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Close", command=self._on_close).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        from orca_workbench.ui.modal import fit_to_content
        fit_to_content(self)   # don't let the controls clip at high UI scaling
        # Populate once realised; matplotlib's own resize keeps the figure
        # fitted to the window (device ratio pinned above).
        self.after(0, self._first_draw)

    def _first_draw(self):
        if self._closed:
            return
        self.update_idletasks()
        self._update_once()
        self._schedule()

    def _schedule(self):
        if self._closed:
            return
        self._after_id = self.after(self.poll_ms, self._tick)

    def _tick(self):
        if not self._closed and self.auto_var.get():
            self._update_once()
        self._schedule()

    def _read(self):
        if not self.out_path or not os.path.isfile(self.out_path):
            return None
        try:
            with open(self.out_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except IOError:
            return None

    def _update_once(self):
        text = self._read()
        if text is None:
            self.status_var.set("Waiting for the output file to appear...")
            return
        parsed = orca_parser.parse_orca_output(text)
        self._last_parsed = parsed
        self.status_var.set(orca_parser.short_status(parsed))
        self._redraw(parsed)
        # Stop auto-refreshing once the job is clearly done.
        if parsed.get("terminated_normally") or parsed.get("has_error"):
            self.auto_var.set(False)

    def _redraw(self, parsed):
        fe = parsed["final_energies"]
        rms = parsed["rms_grads"]
        mx = parsed["max_grads"]
        scf = parsed["scf_iterations"]

        self.ax_energy.clear()
        self.ax_conv.clear()

        if len(fe) > 1:
            self.ax_energy.plot(range(1, len(fe) + 1), fe, marker="o", color="#1f77b4")
            self.ax_energy.set_xlabel("geometry step")
            self.ax_energy.set_ylabel("energy (Eh)")
            self.ax_energy.set_title("Optimization energy ({} steps)".format(len(fe)))
        elif scf:
            self.ax_energy.plot(range(1, len(scf) + 1), scf, marker=".", color="#1f77b4")
            self.ax_energy.set_xlabel("SCF iteration")
            self.ax_energy.set_ylabel("energy (Eh)")
            self.ax_energy.set_title("SCF convergence (latest cycle, {} iters)".format(len(scf)))
        elif fe:
            self.ax_energy.axhline(fe[-1], color="#1f77b4")
            self.ax_energy.set_title("Single-point energy: {:.8f} Eh".format(fe[-1]))
        else:
            self.ax_energy.set_title("(no energies parsed yet)")

        if rms or mx:
            if rms:
                self.ax_conv.semilogy(range(1, len(rms) + 1), rms, marker="o",
                                      label="RMS gradient", color="#d62728")
            if mx:
                self.ax_conv.semilogy(range(1, len(mx) + 1), mx, marker="s",
                                      label="MAX gradient", color="#ff7f0e")
            self.ax_conv.set_xlabel("geometry step")
            self.ax_conv.set_ylabel("gradient (log)")
            self.ax_conv.legend(loc="best", fontsize=8)
            self.ax_conv.set_title("Gradient convergence")
        else:
            self.ax_conv.set_title("(no gradient data — single point, or not yet at first step)")

        self.canvas.draw_idle()

    def _on_close(self):
        self._closed = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self.destroy()


_HARTREE_KCAL = 627.5094740631


class ScanPlotWindow(tk.Toplevel):
    """Static energy profile of a relaxed surface scan: energy vs the scanned
    coordinate. Defaults to ΔE (kcal/mol) relative to the lowest point (what you
    usually want from a scan), with a toggle to absolute Hartree."""

    def __init__(self, parent, title, points, xlabel="scan coordinate"):
        # type: (tk.Misc, str, list, str) -> None
        super().__init__(parent)
        self.title("Scan profile - {}".format(title))
        self.geometry("820x620")
        self._points = [p for p in (points or []) if "coordinate" in p and "energy" in p]
        self._xlabel = xlabel
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as e:
            ttk.Label(self, text="Could not initialise matplotlib:\n  {}\n\n"
                      "Install it:  pip install --user matplotlib".format(e),
                      justify=tk.LEFT, wraplength=520).pack(padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=(0, 12))
            return
        if not self._points:
            ttk.Label(self, text="No scan surface found in this calculation's output.").pack(
                padx=20, pady=20)
            ttk.Button(self, text="Close", command=self.destroy).pack(pady=(0, 12))
            return

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        self._rel = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Relative to minimum (kcal/mol)", variable=self._rel,
                        command=self._draw).pack(side=tk.LEFT)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        self._summary = tk.StringVar()
        ttk.Label(bar, textvariable=self._summary, foreground="#444").pack(side=tk.LEFT, padx=12)

        self.fig = Figure(figsize=(7.4, 5.0), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        _pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        from orca_workbench.ui.modal import fit_to_content
        fit_to_content(self)
        self.after(0, self._draw)

    def _draw(self):
        xs = [p["coordinate"] for p in self._points]
        e_h = [p["energy"] for p in self._points]
        emin = min(e_h)
        imin = e_h.index(emin)
        rel = self._rel.get()
        ys = [(e - emin) * _HARTREE_KCAL for e in e_h] if rel else e_h
        self.ax.clear()
        self.ax.plot(xs, ys, marker="o", color="#1f77b4", lw=1.4)
        self.ax.plot([xs[imin]], [ys[imin]], marker="o", color="#d62728", ms=9,
                     label="minimum")
        self.ax.set_xlabel(self._xlabel)
        self.ax.set_ylabel("ΔE (kcal/mol)" if rel else "energy (Eh)")
        self.ax.set_title("Relaxed scan energy profile ({} points)".format(len(xs)))
        self.ax.legend(loc="best", fontsize=8)
        span = (max(ys) - min(ys)) if rel else (max(e_h) - min(e_h)) * _HARTREE_KCAL
        self.summary_text = "min at {} = {:.6f} Eh; barrier span {:.2f} kcal/mol".format(
            xs[imin], emin, span)
        self._summary.set(self.summary_text)
        self.canvas.draw_idle()


def open_scan_plot(parent, title, out_text, xlabel="scan coordinate"):
    """Parse a scan surface from an ORCA .out and show the profile. Returns the
    window, or None (after a message) if there's no scan data."""
    from orca_workbench.core import orca_parser
    pts = orca_parser.parse_relaxed_scan(out_text or "")
    return ScanPlotWindow(parent, title, pts, xlabel=xlabel)
