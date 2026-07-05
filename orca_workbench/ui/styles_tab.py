"""Styles / Skins gallery — pick the app's colour theme.

A gallery of skin "cards", each with a live mini-preview and a radio button.
Selecting one applies it to the whole app immediately (and persists it to the
per-user config). The default skin is the untouched native look; the coloured
skins (Dark, Frutiger Aero, Boombox) are painted by ``ui/theming``.

Hosted in a dialog opened by the right-aligned toolbar **Styles...** button
(``App.on_open_styles``) — deliberately NOT a notebook tab, since appearance
isn't part of the fundamental pipeline. Not offered in ``--simple`` mode —
styling is off the gateway/low-latency path (see the roadmap).
"""

import os

import tkinter as tk
from tkinter import ttk

from orca_workbench.core import theme as theme_mod
from orca_workbench.ui.tooltip import tip


class StylesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._skin_var = tk.StringVar(value=theme_mod.active_skin_id())
        self._previews = {}   # skin_id -> Canvas (redrawn on refresh)
        self._build()

    def _build(self):
        header = ttk.Frame(self)
        header.pack(side=tk.TOP, fill=tk.X, padx=14, pady=(12, 4))
        ttk.Label(header, text="Appearance",
                  font=("TkDefaultFont", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(header, wraplength=760, justify=tk.LEFT, foreground="#555",
                  text="Pick a skin — it applies to the whole app straight away and is "
                       "remembered between sessions. The default skin is the classic native "
                       "look; the others re-colour every panel, table and menu.").pack(
                           anchor=tk.W, pady=(2, 0))

        # Custom-skin toolbar: JSON skin files live in ~/.orca_workbench_skins/.
        tools = ttk.Frame(self)
        tools.pack(side=tk.TOP, fill=tk.X, padx=14, pady=(0, 2))
        ttk.Button(tools, text="New custom skin...", command=self._new_custom_skin).pack(
            side=tk.LEFT, padx=(0, 4))
        ttk.Button(tools, text="Open skins folder", command=self._open_skins_folder).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(tools, text="Reload skins", command=self._reload_skins).pack(
            side=tk.LEFT, padx=4)

        # Scrollable card list (future-proof for more skins than fit).
        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=6)
        canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._cards_canvas = canvas
        cards = ttk.Frame(canvas)
        self._cards_frame = cards
        wid = canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(wid, width=e.width))

        self._populate_cards()

        note = ttk.Label(
            self, wraplength=780, justify=tk.LEFT, foreground="#777",
            text="Custom skins are JSON files in ~/.orca_workbench_skins/. A skin need only "
                 "carry an id, a base ('default'/'dark'/'aero'/'boombox'), and the colours you "
                 "want to change — everything else (button hover, tab highlight, selection) is "
                 "derived from the palette. Not loaded in --simple / gateway mode.")
        note.pack(side=tk.BOTTOM, anchor=tk.W, padx=14, pady=(4, 10))

    def _populate_cards(self):
        for w in self._cards_frame.winfo_children():
            w.destroy()
        self._previews = {}
        for skin in theme_mod.all_skins():
            self._make_card(self._cards_frame, skin)
        # Bind the wheel AFTER the cards exist, so scrolling works over the cards
        # too (Tk wheel events don't bubble to the parent canvas, and the cards
        # are added after the canvas — so an earlier bind would miss them).
        try:
            from orca_workbench.ui.shortcuts import bind_mousewheel
            bind_mousewheel(self._cards_canvas, self._cards_canvas)
        except Exception:
            pass

    def _new_custom_skin(self):
        import os
        from tkinter import messagebox, simpledialog
        base = simpledialog.askstring(
            "New custom skin",
            "Start from which built-in skin?\n(default / dark / aero / boombox)",
            initialvalue=theme_mod.active_skin_id(), parent=self)
        if base is None:
            return
        base = base.strip() or "default"
        if base not in theme_mod.skin_ids():
            base = "default"
        d = theme_mod.user_skins_dir()
        path = os.path.join(d, "my_skin.json")
        n = 2
        while os.path.exists(path):
            path = os.path.join(d, "my_skin_{}.json".format(n))
            n += 1
        try:
            theme_mod.write_skin_template(path, base_id=base)
        except OSError as e:
            messagebox.showerror("New custom skin", "Could not write:\n{}".format(e), parent=self)
            return
        theme_mod.reload_user_skins()
        self._populate_cards()
        messagebox.showinfo(
            "New custom skin",
            "Wrote a starter skin to:\n{}\n\nEdit its colours in a text editor, change its "
            "\"id\"/\"label\", then click 'Reload skins'. It'll appear as a new card.".format(path),
            parent=self)
        self._open_path(d)

    def _open_skins_folder(self):
        import os
        d = theme_mod.user_skins_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        self._open_path(d)

    def _open_path(self, path):
        import subprocess
        import sys
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _reload_skins(self):
        theme_mod.reload_user_skins()
        self._populate_cards()
        if hasattr(self.app, "set_status"):
            self.app.set_status("Reloaded custom skins.")

    def _make_card(self, parent, skin):
        sid = skin["id"]
        card = ttk.Frame(parent, relief=tk.GROOVE, borderwidth=1, padding=8)
        card.pack(side=tk.TOP, fill=tk.X, padx=6, pady=5)

        preview = tk.Canvas(card, width=180, height=104, highlightthickness=1,
                            highlightbackground="#999999", borderwidth=0)
        preview.pack(side=tk.LEFT, padx=(2, 12), pady=2)
        self._previews[sid] = preview
        _draw_preview(preview, skin)

        right = ttk.Frame(card)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rb = ttk.Radiobutton(right, text=skin["label"], value=sid,
                             variable=self._skin_var,
                             command=lambda s=sid: self._on_pick(s))
        rb.pack(anchor=tk.W)
        try:
            rb.configure(style="Skin.TRadiobutton")
        except tk.TclError:
            pass
        ttk.Label(right, text=skin["tagline"], wraplength=520,
                  justify=tk.LEFT, foreground="#666").pack(anchor=tk.W, pady=(2, 0))

        # The whole card selects the skin (nicer target than just the radio).
        for w in (card, preview, right):
            w.bind("<Button-1>", lambda e, s=sid: self._on_pick(s))
        tip(preview, "Click to apply the {} skin.".format(skin["label"]))

    def _on_pick(self, skin_id):
        self._skin_var.set(skin_id)
        # Delegate to the app so the whole widget tree is repainted + persisted.
        if hasattr(self.app, "apply_skin"):
            self.app.apply_skin(skin_id)

    def refresh(self):
        # Keep the selection in sync if the skin was changed elsewhere, and redraw
        # the previews (their canvases may have been recoloured by a re-skin pass).
        self._skin_var.set(theme_mod.active_skin_id())
        for sid, canvas in self._previews.items():
            try:
                _draw_preview(canvas, theme_mod.get_skin(sid))
            except tk.TclError:
                pass


def _draw_preview(canvas, skin):
    """Paint a little mock window into `canvas` showing this skin: a titlebar in
    the accent, a surface 'card' with sample text, and a mini button."""
    canvas.delete("all")
    w = int(canvas.cget("width"))
    h = int(canvas.cget("height"))
    win, surf = skin["window"], skin["surface"]
    fg, muted = skin["fg"], skin["muted"]
    accent, afg, border = skin["accent"], skin["accent_fg"], skin["border"]

    # Chrome backdrop (covers the canvas bg so a later re-skin can't bleed through).
    canvas.create_rectangle(0, 0, w, h, fill=win, outline=win)
    # Titlebar.
    canvas.create_rectangle(0, 0, w, 20, fill=accent, outline=accent)
    canvas.create_text(8, 10, text="ORCA Workbench", anchor="w", fill=afg,
                       font=("TkDefaultFont", 8, "bold"))
    # Surface "paper" card.
    canvas.create_rectangle(10, 28, w - 10, h - 12, fill=surf, outline=border)
    canvas.create_text(18, 40, text="Molecule", anchor="w", fill=fg,
                       font=("TkDefaultFont", 9, "bold"))
    canvas.create_text(18, 56, text="benzene · C6H6", anchor="w", fill=muted,
                       font=("TkDefaultFont", 8))
    # Selected row.
    canvas.create_rectangle(16, 66, w - 16, 80, fill=skin["select_bg"], outline="")
    canvas.create_text(18, 73, text="row selected", anchor="w",
                       fill=skin["select_fg"], font=("TkDefaultFont", 8))
    # Mini button.
    canvas.create_rectangle(w - 66, 86, w - 16, h - 4, fill=accent, outline=border)
    canvas.create_text(w - 41, 92, text="Build", fill=afg, font=("TkDefaultFont", 8))
