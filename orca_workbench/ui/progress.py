"""A simple modal progress dialog driven on the main thread.

No background threads (Tkinter isn't thread-safe — threads here would add
race conditions and shutdown hangs for little gain). Instead the caller drives
it in a loop, calling .step() per item; .step() advances a determinate bar and
pumps update_idletasks() so the bar visibly grows. We deliberately use
update_idletasks (redraw only) rather than update() so queued user clicks
aren't re-dispatched mid-operation — that keeps the operation atomic and
re-entrancy-free at the cost of the window not being draggable while it runs.

Usage:
    pd = ProgressDialog(parent, "Submitting jobs", total=len(items))
    for i, item in enumerate(items):
        pd.step("Submitting {}...".format(item.name))
        ... do the slow thing ...
        if pd.cancelled:
            break
    pd.close()
"""

import tkinter as tk
from tkinter import ttk

from orca_workbench.ui.modal import make_modal


class ProgressDialog(tk.Toplevel):
    def __init__(self, parent, title, total, allow_cancel=True):
        # type: (tk.Misc, str, int, bool) -> None
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.total = max(1, int(total))
        self._done = 0
        self.cancelled = False

        ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold")).pack(
            anchor=tk.W, padx=14, pady=(12, 4))
        self.msg_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.msg_var, width=56, anchor=tk.W).pack(
            anchor=tk.W, padx=14)
        self.bar = ttk.Progressbar(self, length=380, mode="determinate", maximum=self.total)
        self.bar.pack(padx=14, pady=8)
        self.count_var = tk.StringVar(value="0 / {}".format(self.total))
        ttk.Label(self, textvariable=self.count_var, foreground="#666").pack(anchor=tk.E, padx=14)

        if allow_cancel:
            btns = ttk.Frame(self)
            btns.pack(fill=tk.X, padx=14, pady=(4, 12))
            ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        make_modal(self, parent)
        self.update_idletasks()

    def step(self, message=""):
        # type: (str) -> None
        """Advance the bar by one and refresh the display. Call once per item
        BEFORE doing that item's work, so the message reflects what's starting."""
        self._done += 1
        if message:
            self.msg_var.set(message)
        try:
            self.bar["value"] = self._done
            self.count_var.set("{} / {}".format(self._done, self.total))
            self.update_idletasks()
        except tk.TclError:
            pass

    def _cancel(self):
        self.cancelled = True
        self.msg_var.set("Cancelling — finishing current item...")

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
