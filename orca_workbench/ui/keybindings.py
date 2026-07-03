"""Settings > Keyboard shortcuts — view / rebind / reset the app's hotkeys.

A scrollable list grouped by category (from core.keymap). Click a shortcut to
capture a new key combination; changes persist to config. Application shortcuts
re-bind live (via the on_change callback); plot-window keys apply to newly opened
plots (they read the keymap when the window is built).
"""

import tkinter as tk
from tkinter import messagebox, ttk

from orca_workbench.core import keymap
from orca_workbench.ui.modal import make_modal
from orca_workbench.ui.shortcuts import bind_mousewheel


class KeybindingsDialog(tk.Toplevel):
    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        self.title("Keyboard shortcuts")
        self.geometry("580x580")
        self._on_change = on_change
        self._capturing = None    # action_id currently capturing a keypress, or None
        self._rows = {}           # action_id -> key button

        ttk.Label(self, text="Click a shortcut, then press the new key combination "
                  "(Esc to cancel). Application shortcuts apply immediately; plot "
                  "shortcuts apply to plot windows opened afterwards.  '*' = customised.",
                  wraplength=545, justify=tk.LEFT, foreground="#444").pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=4)
        canvas = tk.Canvas(body, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(wid, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bind_mousewheel(inner, canvas)
        self._build_rows(inner)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Reset all to defaults", command=self._reset_all).pack(side=tk.LEFT)

        # One capture handler for the whole dialog; only acts while capturing.
        self.bind("<KeyPress>", self._on_capture_key)
        self.bind("<Escape>", lambda e: self._cancel_capture())
        make_modal(self, parent)

    def _build_rows(self, parent):
        r = 0
        for cat, ids in keymap.by_category().items():
            ttk.Label(parent, text=cat, font=("TkDefaultFont", 10, "bold")).grid(
                row=r, column=0, columnspan=3, sticky=tk.W, pady=(10, 2), padx=4)
            r += 1
            for aid in ids:
                ttk.Label(parent, text=keymap.label(aid)).grid(
                    row=r, column=0, sticky=tk.W, padx=(16, 8), pady=2)
                key_btn = ttk.Button(parent, width=20, command=lambda a=aid: self._start_capture(a))
                key_btn.grid(row=r, column=1, sticky=tk.W, pady=2)
                ttk.Button(parent, text="Reset", width=6,
                           command=lambda a=aid: self._reset_one(a)).grid(
                    row=r, column=2, sticky=tk.W, padx=4, pady=2)
                self._rows[aid] = key_btn
                r += 1
        parent.columnconfigure(0, weight=1)
        self._refresh_all()

    def _refresh_all(self):
        for aid, btn in self._rows.items():
            txt = keymap.humanize(keymap.sequence(aid))
            if keymap.is_overridden(aid):
                txt += "  *"
            btn.configure(text=txt)

    def _start_capture(self, aid):
        if self._capturing and self._capturing in self._rows:
            self._refresh_all()   # revert any in-progress capture display
        self._capturing = aid
        self._rows[aid].configure(text="press a key…  (Esc)")
        self.focus_set()

    def _cancel_capture(self):
        if self._capturing:
            self._capturing = None
            self._refresh_all()
        return "break"

    def _on_capture_key(self, event):
        if not self._capturing:
            return None
        if event.keysym == "Escape":
            return self._cancel_capture()
        seq = keymap.event_to_sequence(event.state, event.keysym)
        if seq is None:
            return "break"        # bare modifier — keep waiting for a real key
        aid, self._capturing = self._capturing, None
        clash = keymap.conflicts(aid, seq)
        if clash:
            names = ", ".join(keymap.label(c) for c in clash)
            if not messagebox.askyesno(
                    "Shortcut already in use",
                    "{} is already used by: {}.\n\nAssign it to '{}' anyway? (both would "
                    "then trigger.)".format(keymap.humanize(seq), names, keymap.label(aid)),
                    parent=self):
                self._refresh_all()
                return "break"
        keymap.set_override(aid, seq)
        self._refresh_all()
        self._notify()
        return "break"

    def _reset_one(self, aid):
        keymap.reset(aid)
        self._refresh_all()
        self._notify()

    def _reset_all(self):
        if messagebox.askyesno("Reset all shortcuts",
                               "Reset every shortcut to its default?", parent=self):
            keymap.reset_all()
            self._refresh_all()
            self._notify()

    def _notify(self):
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass
