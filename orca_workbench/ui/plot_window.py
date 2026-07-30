"""Live-progress plot window for a running (or finished) ORCA job.

Embeds matplotlib in a Tk Toplevel and re-reads the job's .out file on a timer,
re-plotting the optimisation energy trajectory, the FULL SCF-iteration history
(every cycle, with opt-cycle boundaries — not just the latest block), and the
gradient convergence. Because the app runs on the cluster and the SLURM .out
streams to the shared filesystem (stdbuf wrapper in the template), this just
reads a local file — no network, no SSH.

Shares the spectrum windows' keyboard navigation (via the rebindable keymap):
Z / P cycle zoom / pan modes (drag on whichever panel the mouse is over,
blitted rubber band), Esc exits, F is the two-stage view reset, R / F5 refresh,
Ctrl+S saves the figure, Ctrl+W closes. A manual zoom survives auto-refresh.

Extras for a running OPT: iteration timing (s per SCF iteration / per opt step,
measured while the window is open) and **View current geometry** — the last
frame of the job's _trj.xyz in the 3D viewer, no need to wait for convergence.
(On the cluster the trajectory lives on node-local /scratch until the copyback,
so mid-run viewing is a local-runner feature; the button explains if absent.)

matplotlib is a soft dependency: if it isn't installed the window explains how
to get it instead of crashing.
"""

import os
import time
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
    _MODE_TEXT = {"zoom_h": "ZOOM horizontal - drag a range (Esc exits)",
                  "zoom_v": "ZOOM vertical - drag a range (Esc exits)",
                  "zoom_box": "ZOOM box - drag a rectangle (Esc exits)",
                  "pan_h": "PAN horizontal - drag (Esc exits)",
                  "pan_v": "PAN vertical - drag (Esc exits)",
                  "pan_free": "PAN free - drag (Esc exits)"}
    _ZOOM_CYCLE = ["zoom_h", "zoom_v", "zoom_box", None]
    _PAN_CYCLE = ["pan_h", "pan_v", "pan_free", None]

    def __init__(self, parent, title, out_path, poll_ms=2000, app=None, trj_path=None,
                 geom_path=None):
        super().__init__(parent)
        self.title("Progress — {}".format(title))
        self.geometry("900x760")
        self.out_path = out_path
        self.poll_ms = poll_ms
        self.app = app
        self.trj_path = trj_path       # expected _trj.xyz of an OPT (may not exist yet)
        self.geom_path = geom_path     # expected optimised <mol>.xyz of an OPT
        self._after_id = None
        self._closed = False
        self._resize_after = None
        self._axes = {}                # panel name -> axes (rebuilt per redraw)
        self._home = {}                # panel name -> (xlim, ylim) data view
        self._nav_mode = None
        self._drag = None
        self._arrivals = []            # (wall time, total SCF iters, opt steps)
        self._last_parsed = None

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

        # Header band: job status / timing / output path on the left, the geometry
        # launchers on the right. The optimised geometry lives HERE (not buried in
        # a right-click menu three tabs away) because this window is what you open
        # to see whether the optimisation is done.
        hdr = ttk.Frame(self)
        hdr.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(6, 2))
        right = ttk.Frame(hdr)
        right.pack(side=tk.RIGHT, anchor=tk.NE, padx=(8, 0))
        left = ttk.Frame(hdr)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_var = tk.StringVar(value="reading...")
        ttk.Label(left, textvariable=self.status_var, anchor=tk.W).pack(side=tk.TOP, fill=tk.X)
        # Iteration timing — measured from data arriving while this window is open.
        self.rate_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.rate_var, foreground="#446", anchor=tk.W).pack(
            side=tk.TOP, fill=tk.X)
        ttk.Label(left, text=out_path, foreground="#666", anchor=tk.W).pack(
            side=tk.TOP, fill=tk.X)
        if self.geom_path:
            b = ttk.Button(right, text="Optimized geometry (3D)...",
                           command=self._view_optimized_geometry)
            b.pack(side=tk.TOP, anchor=tk.E)
            from orca_workbench.ui.tooltip import tip
            tip(b, "Open the converged geometry (<mol>.xyz) in your external 3D viewer. "
                   "Appears for optimisation jobs; it exists once the job has finished.")
        if self.trj_path:
            b = ttk.Button(right, text="Trajectory (movie)...",
                           command=self._view_trajectory)
            b.pack(side=tk.TOP, anchor=tk.E, pady=(2, 0))
            from orca_workbench.ui.tooltip import tip
            tip(b, "Open the whole optimisation as a multi-frame .xyz movie in the "
                   "trajectory viewer (PyMOL / molden / Avogadro).")

        self.fig = Figure(figsize=(7.2, 5.4), dpi=100)
        try:
            self.fig.set_layout_engine("tight")
        except Exception:
            pass
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        _pin_device_pixel_ratio(self.canvas)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="Auto-refresh (every {}s)".format(poll_ms // 1000),
                        variable=self.auto_var).pack(side=tk.LEFT)
        ttk.Button(btns, text="Refresh now", command=self._update_once).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Save image...", command=self._save).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="Open .out (text)", command=self._open_out_text).pack(
            side=tk.LEFT, padx=2)
        if self.trj_path:
            ttk.Button(btns, text="View current geometry (3D)...",
                       command=self._view_current_geometry).pack(side=tk.LEFT, padx=6)
        self._mode_label = ttk.Label(btns, text="", foreground="#146")
        self._mode_label.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Close", command=self._on_close).pack(side=tk.RIGHT)

        self._bind_plot_keys()
        self.canvas.mpl_connect("button_press_event", self._drag_press)
        self.canvas.mpl_connect("motion_notify_event", self._drag_motion)
        self.canvas.mpl_connect("button_release_event", self._drag_release)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        from orca_workbench.ui.modal import fit_to_content, maximize
        fit_to_content(self)   # don't let the controls clip at high UI scaling
        # Open maximised: three stacked panels plus the control row need the room,
        # and on a remote desktop a window taller than the screen loses its bottom
        # half behind the desktop panel (see modal.maximize).
        maximize(self)
        # Populate once realised; matplotlib's own resize keeps the figure
        # fitted to the window (device ratio pinned above).
        self.after(0, self._first_draw)

    # ------------------------------------------------------------- keyboard
    def _bind_action(self, widget, action_id, fn):
        """Bind a rebindable plot action's current key sequence(s) — same
        catalogue as the spectrum windows, read at open time."""
        from orca_workbench.core import keymap
        for seq in keymap.sequence_variants(keymap.sequence(action_id)):
            widget.bind(seq, lambda e, f=fn: (f(), "break")[1])

    def _bind_plot_keys(self):
        w = self.canvas.get_tk_widget()
        w.bind("<Enter>", lambda e: w.focus_set(), add="+")
        self._bind_action(w, "plot.reset", self._key_full)
        self._bind_action(w, "plot.zoom", lambda: self._cycle_nav(self._ZOOM_CYCLE))
        self._bind_action(w, "plot.pan", lambda: self._cycle_nav(self._PAN_CYCLE))
        self._bind_action(w, "plot.redraw", self._update_once)
        w.bind("<Escape>", lambda e: (self._set_nav_mode(None), "break")[1])
        self.bind("<F5>", lambda e: (self._update_once(), "break")[1])
        self._bind_action(self, "plot.save_image", self._save)
        self._bind_action(self, "plot.close", self._on_close)

    def _cycle_nav(self, cycle):
        cur = self._nav_mode if self._nav_mode in cycle else None
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else cycle[0]
        self._set_nav_mode(nxt)

    def _set_nav_mode(self, mode):
        self._drag = None
        self._nav_mode = mode
        try:
            self._mode_label.configure(text=self._MODE_TEXT.get(mode, ""))
        except tk.TclError:
            pass

    @staticmethod
    def _lims_close(a, b):
        span = abs(b[1] - b[0]) or 1.0
        return abs(a[0] - b[0]) < span * 1e-3 and abs(a[1] - b[1]) < span * 1e-3

    def _key_full(self):
        """Two-stage reset across every panel: first all X to the data view,
        then all Y (same convention as the spectrum windows)."""
        xs_home = all(self._lims_close(ax.get_xlim(), self._home[k][0])
                      for k, ax in self._axes.items() if k in self._home)
        for k, ax in self._axes.items():
            home = self._home.get(k)
            if not home:
                continue
            if not xs_home:
                ax.set_xlim(home[0])
            else:
                ax.set_ylim(home[1])
        self.canvas.draw_idle()

    def _save(self):
        if getattr(self, "fig", None) is None:
            return
        from orca_workbench.ui.spectra import _save_figure
        _save_figure(self.fig, self)

    def _open_out_text(self):
        """Open the raw .out in the user's configured text editor. ORCA's text output
        streams to the shared-FS .out even on the cluster (SLURM --output + line
        buffering), so this works for a RUNNING job; it's only missing while the job is
        still queued (PENDING) or for a purely local run that hasn't started."""
        from tkinter import messagebox
        if not (self.out_path and os.path.isfile(self.out_path)):
            messagebox.showinfo(
                "No output on disk yet",
                "The output file isn't here yet:\n{}\n\nIf this is a cluster job it may "
                "still be queued (PENDING) — ORCA's text output streams to the shared "
                "filesystem once the job starts RUNNING. (Auxiliary files like the "
                "trajectory / .gbw stay on the compute node's scratch until the job "
                "finishes and are copied back then.)".format(self.out_path or "(unknown)"),
                parent=self)
            return
        from orca_workbench.ui import extprog
        extprog.open_with(self, "text_editor_path", self.out_path, "text editor",
                          "Choose a plain-text editor for ORCA .out / .inp files "
                          "(Notepad++, VS Code, gedit, ...).")

    # ------------------------------------------------- zoom/pan (multi-panel)
    def _drag_press(self, event):
        if (not self._nav_mode or event.button != 1 or event.inaxes is None
                or event.xdata is None or event.ydata is None):
            return
        ax = event.inaxes
        self._drag = {"ax": ax, "x0": event.xdata, "y0": event.ydata,
                      "xlim": ax.get_xlim(), "ylim": ax.get_ylim(),
                      "inv": ax.transData.inverted(), "mode": self._nav_mode}
        if self._nav_mode.startswith("zoom"):
            # Blitted rubber band — one snapshot, re-blit per move (ThinLinc-safe).
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
        if event.inaxes is not d["ax"]:
            return
        ax = d["ax"]
        mode = d["mode"]
        if mode.startswith("pan"):
            inv = d.get("inv")
            if inv is None:
                return
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
        ax = d["ax"]
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

        def set_lim(setter, getter, a, b):
            lo, hi = min(a, b), max(a, b)
            cur = getter()
            setter((hi, lo) if cur[0] > cur[1] else (lo, hi))

        if mode in ("zoom_h", "zoom_box") and abs(xe - x0) > 1e-12:
            set_lim(ax.set_xlim, ax.get_xlim, x0, xe)
        if mode in ("zoom_v", "zoom_box") and abs(ye - y0) > 1e-12:
            set_lim(ax.set_ylim, ax.get_ylim, y0, ye)
        self.canvas.draw_idle()

    # ------------------------------------------------------------- polling
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
        self._note_arrival(parsed)
        self._redraw(parsed)
        # Stop auto-refreshing once the job is clearly done.
        if parsed.get("terminated_normally") or parsed.get("has_error"):
            self.auto_var.set(False)

    def _note_arrival(self, parsed):
        """Track when new iterations appear, to estimate iteration timing.
        Only what arrives while THIS window is open can be timed — ORCA doesn't
        timestamp iterations, so pre-existing history has no time axis."""
        n_scf = sum(len(b) for b in parsed.get("scf_blocks") or [])
        n_steps = len(parsed.get("final_energies") or [])
        now = time.time()
        if not self._arrivals or (n_scf, n_steps) != self._arrivals[-1][1:]:
            self._arrivals.append((now, n_scf, n_steps))
            if len(self._arrivals) > 400:
                del self._arrivals[:200]
        parts = []
        a = self._arrivals
        if len(a) >= 2:
            t0, s0, c0 = a[0]
            t1, s1, c1 = a[-1]
            if s1 > s0:
                parts.append("~{:.1f} s / SCF iteration".format((t1 - t0) / (s1 - s0)))
            if c1 > c0:
                parts.append("~{:.0f} s / opt step".format((t1 - t0) / (c1 - c0)))
        if a:
            ago = now - a[-1][0]
            if ago >= 1.5:
                parts.append("last new data {:.0f} s ago".format(ago))
        done = parsed.get("terminated_normally") or parsed.get("has_error")
        self.rate_var.set(("timing (since window opened): " + "  ·  ".join(parts))
                          if parts and not done else "")

    # ------------------------------------------------------------- drawing
    @staticmethod
    def _corner_title(ax, text):
        """A panel caption INSIDE the axes, upper right, in a small font — instead
        of a big set_title() band above every subplot. Three stacked panels spend
        the reclaimed height on actual data."""
        ax.text(0.995, 0.94, text, transform=ax.transAxes, ha="right", va="top",
                fontsize=8, color="#333333",
                bbox={"facecolor": "white", "alpha": 0.65, "edgecolor": "none",
                      "pad": 1.5})

    def _redraw(self, parsed):
        fe = parsed["final_energies"]
        rms = parsed["rms_grads"]
        mx = parsed["max_grads"]
        blocks = parsed.get("scf_blocks") or []

        # Preserve a manual zoom/pan across the auto-refresh: remember which
        # panels were moved off their data view, restore after rebuilding.
        keep = {}
        for name, ax in self._axes.items():
            home = self._home.get(name)
            if home and (not self._lims_close(ax.get_xlim(), home[0])
                         or not self._lims_close(ax.get_ylim(), home[1])):
                keep[name] = (ax.get_xlim(), ax.get_ylim())

        # Panels, top to bottom: opt energy, gradients, SCF history. Energy and
        # gradients are both per GEOMETRY STEP, so they're adjacent and SHARE the
        # x-axis: one set of tick labels and one xlabel for the pair, which buys
        # back a chunk of vertical space (the SCF panel has its own iteration
        # axis, so it sits below). A running SP job with SCF data only gets that
        # panel full height.
        panels = []
        if fe or not blocks:
            panels.append("energy")
        if rms or mx:
            panels.append("conv")
        if blocks:
            panels.append("scf")

        self.fig.clear()
        self._axes = {}
        n = len(panels)
        for i, name in enumerate(panels):
            share = self._axes.get("energy") if name == "conv" else None
            self._axes[name] = self.fig.add_subplot(n, 1, i + 1, sharex=share)
        shared = "energy" in self._axes and "conv" in self._axes

        ax = self._axes.get("energy")
        if ax is not None:
            if len(fe) > 1:
                ax.plot(range(1, len(fe) + 1), fe, marker="o", color="#1f77b4")
                if not shared:
                    ax.set_xlabel("geometry step")
                ax.set_ylabel("energy (Eh)")
                self._corner_title(ax, "OPT energy - {} steps".format(len(fe)))
            elif len(fe) == 1:
                ax.axhline(fe[-1], color="#1f77b4")
                self._corner_title(ax, "single point: {:.8f} Eh".format(fe[-1]))
            else:
                self._corner_title(ax, "(no energies parsed yet)")
            if shared:
                # the gradient panel right below carries the tick labels
                ax.tick_params(labelbottom=False)

        ax = self._axes.get("conv")
        if ax is not None:
            if rms:
                ax.semilogy(range(1, len(rms) + 1), rms, marker="o",
                            label="RMS gradient", color="#d62728")
            if mx:
                ax.semilogy(range(1, len(mx) + 1), mx, marker="s",
                            label="MAX gradient", color="#ff7f0e")
            ax.set_xlabel("geometry step")
            ax.set_ylabel("gradient (log)")
            # lower left: out of the way of both the corner title and the curves,
            # which decay towards the bottom RIGHT as the optimisation converges
            ax.legend(loc="lower left", fontsize=7, framealpha=0.7)
            self._corner_title(ax, "gradient convergence")

        ax = self._axes.get("scf")
        if ax is not None:
            xs, ys, bounds = [], [], []
            k = 0
            for b in blocks:
                if k:
                    bounds.append(k + 0.5)
                xs.extend(range(k + 1, k + len(b) + 1))
                ys.extend(b)
                k += len(b)
            ax.plot(xs, ys, marker=".", ms=3, lw=0.9, color="#2ca02c")
            for x in bounds:
                ax.axvline(x, color="#bbbbbb", lw=0.7, linestyle="--")
            ax.set_xlabel("SCF iteration (cumulative over {} cycle{})".format(
                len(blocks), "" if len(blocks) == 1 else "s"))
            ax.set_ylabel("energy (Eh)")
            self._corner_title(ax, "SCF - {} iters / {} cycle{}".format(
                k, len(blocks), "" if len(blocks) == 1 else "s"))

        for name, a in self._axes.items():
            self._home[name] = (a.get_xlim(), a.get_ylim())
        for name, lims in keep.items():
            a = self._axes.get(name)
            if a is not None:
                a.set_xlim(lims[0])
                a.set_ylim(lims[1])

        self.canvas.draw_idle()

    # ------------------------------------------------- geometry (OPT) launchers
    def _view_optimized_geometry(self):
        """Open the CONVERGED geometry (<mol>.xyz beside the .out) in the external
        3D viewer — the thing you actually want when an optimisation finishes."""
        from tkinter import messagebox
        p = self.geom_path
        if not p or not os.path.isfile(p):
            messagebox.showinfo(
                "No optimised geometry yet",
                "ORCA writes the converged geometry here when the optimisation "
                "finishes:\n{}\n\nIt isn't there yet. While the job runs, use "
                "'View current geometry' (the last trajectory frame) instead; on the "
                "cluster the trajectory only appears after the job ends and copies "
                "back from the node's scratch.".format(p or "(unknown)"), parent=self)
            return
        from orca_workbench.ui.molecules_tab import open_xyz_3d
        open_xyz_3d(self, self.app, p)

    def _view_trajectory(self):
        """Open the full optimisation trajectory (multi-frame .xyz) as a movie."""
        from tkinter import messagebox
        p = self.trj_path
        if not p or not os.path.isfile(p):
            messagebox.showinfo(
                "No trajectory yet",
                "The trajectory file isn't here:\n{}\n\nA local run writes it after the "
                "first optimisation step; a cluster job copies it back when it "
                "ends.".format(p or "(unknown)"), parent=self)
            return
        from orca_workbench.ui.molecules_tab import open_xyz_3d
        open_xyz_3d(self, self.app, p, slot="traj_viewer_path")

    def _view_current_geometry(self):
        """Open the LAST frame of the job's _trj.xyz — the current geometry of a
        still-running optimisation — in the external 3D viewer."""
        from tkinter import messagebox
        p = self.trj_path
        if not p or not os.path.isfile(p):
            messagebox.showinfo(
                "No trajectory yet",
                "The trajectory file hasn't appeared:\n{}\n\nA local run writes it after "
                "the first opt step. On the cluster the job runs on node-local /scratch "
                "and only copies back when it ENDS — mid-run geometries aren't visible "
                "from the gateway.".format(p or "(unknown)"), parent=self)
            return
        try:
            from orca_workbench.core import coords as coords_mod
            frames = coords_mod._read_xyz_frames(p)
            if not frames:
                raise ValueError("no frames in {}".format(p))
            atoms, _meta = frames[-1]
            import tempfile
            tdir = tempfile.mkdtemp(prefix="orca_wb_current_")
            cur = os.path.join(tdir, "current_step_{:03d}.xyz".format(len(frames)))
            coords_mod.write_xyz(cur, atoms,
                                 {"name": "current geometry",
                                  "comment": "opt step {} (job still running)".format(
                                      len(frames))})
        except Exception as e:
            messagebox.showwarning("Current geometry", str(e), parent=self)
            return
        from orca_workbench.ui.molecules_tab import open_xyz_3d
        open_xyz_3d(self, self.app, cur)

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
