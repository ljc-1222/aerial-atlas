"""Quiet desktop chrome, live themes, localization, and on-demand preferences."""

import ctypes
import os
import tkinter as tk
from tkinter import ttk
import time

from .viewer_preferences import THEMES, STRINGS, map_background, system_theme
from . import __version__


class ViewerUI:
    def tr(self, key, **values):
        return STRINGS[self.preferences.language][key].format(**values)

    def _text(self, parent, key, kind=ttk.Label, **options):
        widget = kind(parent, text=self.tr(key), **options)
        self.translated.append((widget, key))
        return widget

    def _set_status(self, key, **values):
        self.status_key, self.status_values = key, values
        self.status.set(self.tr(key, **values))

    def _build_ui(self):
        self.translated = []
        self.tinted = []
        self.settings_window = self.info_window = None
        self.settings_timer = self.save_after = None
        self.fullscreen = False
        self.theme_name = ""
        self.next_system_check = 0
        root = self.root
        prefs = self.preferences
        width = min(prefs.window_width if prefs.remember_view else 1480, root.winfo_screenwidth())
        height = min(prefs.window_height if prefs.remember_view else 940, root.winfo_screenheight())
        root.geometry(f"{width}x{height}")
        root.minsize(960, 640)
        root.maxsize(root.winfo_screenwidth(), root.winfo_screenheight())
        self.style = ttk.Style(root)
        self.style.theme_use("clam")

        header = ttk.Frame(root, padding=(18, 10))
        header.pack(fill="x")
        self.mark = tk.Canvas(header, width=25, height=28, highlightthickness=0)
        self.mark.pack(side="left", padx=(0, 10))
        self._text(header, "app", font=("Microsoft JhengHei UI", 13, "bold")).pack(side="left")
        self.help_menu = tk.Menu(root, tearoff=False)
        self.view_menu = tk.Menu(root, tearoff=False)
        self.tile_menu = tk.Menu(root, tearoff=False)
        self._text(header, "help", kind=ttk.Menubutton, menu=self.help_menu).pack(side="right")
        self._text(header, "settings", kind=ttk.Button, command=self.show_settings, style="Quiet.TButton").pack(side="right", padx=6)
        self._text(header, "view", kind=ttk.Menubutton, menu=self.view_menu).pack(side="right")

        body = tk.Frame(root)
        body.pack(fill="both", expand=True)
        self.tinted.append((body, "line"))
        self.sidebar = ttk.Frame(body, width=312, padding=(18, 18, 18, 12))
        self.sidebar.pack(side="left", fill="y", padx=(0, 1))
        self.sidebar.pack_propagate(False)
        self._text(self.sidebar, "search", style="Section.TLabel").pack(anchor="w", pady=(0, 12))
        self._text(self.sidebar, "search_hint", style="Muted.TLabel", wraplength=274, justify="left").pack(anchor="w", pady=(0, 9))
        search_row = ttk.Frame(self.sidebar)
        search_row.pack(fill="x")
        self.query = tk.StringVar()
        self.entry = ttk.Entry(search_row, textvariable=self.query, font=("Segoe UI", 10), width=14)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda event: self.locate_text(self.query.get()))
        self.entry.bind("<Down>", self._focus_list)
        self.entry.bind("<Escape>", self.clear_search)
        self.query.trace_add("write", self._query_changed)
        self.search_button = self._text(search_row, "locate", kind=ttk.Button, style="Accent.TButton", command=lambda: self.locate_text(self.query.get()))
        self.search_button.pack(side="right", padx=(7, 0))
        self.search_error = tk.StringVar()
        self.search_error_key = None
        self.error_label = ttk.Label(self.sidebar, textvariable=self.search_error, style="Error.TLabel", wraplength=274)
        self.error_anchor = search_row

        scope = ttk.Frame(self.sidebar)
        scope.pack(fill="x", pady=(12, 0))
        self._text(scope, "row_filter", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        self.row_combo = ttk.Combobox(scope, state="readonly", width=16)
        self.row_combo.pack(side="left", fill="x", expand=True)
        self.row_combo.bind("<<ComboboxSelected>>", self._row_changed)

        details_panel = ttk.Frame(self.sidebar)
        details_panel.pack(side="bottom", fill="x", pady=(12, 0))
        self.details_button = ttk.Button(details_panel, command=self.toggle_details, style="Quiet.TButton")
        self.details_button.pack(fill="x")
        self.details_content = ttk.Frame(details_panel, padding=(0, 8, 0, 0))
        self.tile_name = tk.StringVar(value=self.tr("no_selection"))
        ttk.Label(self.details_content, textvariable=self.tile_name, font=("Segoe UI", 9), wraplength=274).pack(anchor="w", pady=(0, 8))
        self.detail_grid = ttk.Frame(self.details_content)
        self.detail_grid.pack(fill="x")
        self.detail_fields = {}
        for row, key in enumerate(("grid", "origin", "size", "center")):
            self._text(self.detail_grid, key, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=3)
            self.detail_fields[key] = tk.StringVar(value="—")
            ttk.Label(self.detail_grid, textvariable=self.detail_fields[key], font=("Segoe UI", 9)).grid(row=row, column=1, sticky="e", pady=3)
        self.detail_grid.columnconfigure(1, weight=1)
        navigation = ttk.Frame(self.sidebar)
        navigation.pack(side="bottom", fill="x", pady=(12, 0))
        self.previous_button = self._text(navigation, "previous_tile", kind=ttk.Button, style="Browse.TButton", command=lambda: self.browse_step(-1))
        self.previous_button.grid(row=0, column=0, sticky="w")
        self.browse_position = tk.StringVar()
        ttk.Label(navigation, textvariable=self.browse_position, font=("Segoe UI", 9), anchor="center").grid(row=0, column=1, sticky="ew", padx=5)
        self.next_button = self._text(navigation, "next_tile", kind=ttk.Button, style="Browse.TButton", command=lambda: self.browse_step(1))
        self.next_button.grid(row=0, column=2, sticky="e")
        navigation.columnconfigure(1, weight=1)
        listing_header = ttk.Frame(self.sidebar)
        listing_header.pack(fill="x", pady=(20, 8))
        self._text(listing_header, "tile_ids", style="Muted.TLabel").pack(side="left")
        self.match_text = tk.StringVar()
        ttk.Label(listing_header, textvariable=self.match_text, style="Muted.TLabel").pack(side="right")
        listing = ttk.Frame(self.sidebar)
        listing.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(listing, font=("Segoe UI", 10), borderwidth=0, highlightthickness=1,
                                 exportselection=False, activestyle="dotbox", selectborderwidth=0)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.bind("<ButtonRelease-1>", self._click_list)
        self.listbox.bind("<Return>", self._locate_list)
        self.listbox.insert("end", *(tile.identifier for tile in self.filtered))
        self.empty_label = self._text(listing, "no_browse_results", style="Muted.TLabel", wraplength=230, justify="center")

        self.map_area = tk.Frame(body)
        self.map_area.pack(side="left", fill="both", expand=True)
        self.tinted.append((self.map_area, "map"))
        self.canvas = tk.Canvas(self.map_area, highlightthickness=1, cursor="crosshair", takefocus=True)
        self.canvas.pack(fill="both", expand=True)
        for event, command in (("<Configure>", self._resize), ("<ButtonPress-1>", self._drag_start),
                               ("<B1-Motion>", self._drag), ("<ButtonRelease-1>", self._drag_end),
                               ("<Motion>", self._mouse_position), ("<MouseWheel>", self._wheel)):
            self.canvas.bind(event, command)
        self.canvas.bind("<Button-4>", lambda event: self._zoom(1.2, event.x, event.y))
        self.canvas.bind("<Button-5>", lambda event: self._zoom(1 / 1.2, event.x, event.y))
        for key, factor in (("plus", 1.25), ("equal", 1.25), ("minus", 1 / 1.25)):
            self.canvas.bind(f"<Key-{key}>", lambda event, factor=factor: self.zoom_center(factor))
        for key, dx, dy in (("Left", -100, 0), ("Right", 100, 0), ("Up", 0, -100), ("Down", 0, 100)):
            self.canvas.bind(f"<Key-{key}>", lambda event, dx=dx, dy=dy: self.pan_by(dx, dy))
        self.canvas.bind("<Home>", lambda event: self.fit_all())
        self.canvas.bind("<Button-3>", self._map_context_menu)
        self.canvas.bind("<Shift-F10>", self._map_context_menu)
        for widget in (root, self.listbox):
            widget.bind("<Prior>", lambda event: self.browse_step(-1))
            widget.bind("<Next>", lambda event: self.browse_step(1))
        self.loading_item = self.canvas.create_text(100, 100, text=self.tr("loading"), font=("Microsoft JhengHei UI", 11), tags="loading")
        zoom_tools = ttk.Frame(self.map_area, padding=2)
        zoom_tools.place(relx=1, x=-16, y=16, anchor="ne")
        ttk.Button(zoom_tools, text="+", command=lambda: self.zoom_center(1.25), style="Zoom.TButton").pack()
        ttk.Button(zoom_tools, text="−", command=lambda: self.zoom_center(1 / 1.25), style="Zoom.TButton").pack()
        self.minimap_frame = ttk.Frame(self.map_area, padding=6)
        self.minimap = tk.Canvas(self.minimap_frame, width=152, height=176, highlightthickness=0, cursor="hand2")
        self.minimap.pack()
        self.minimap.bind("<Button-1>", self._minimap_click)

        footer = ttk.Frame(root, padding=(18, 6))
        footer.pack(fill="x")
        self.status, self.position, self.cache_status, self.scale_text = (tk.StringVar() for _ in range(4))
        self._set_status("preparing")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self.scale_text, font=("Segoe UI", 9)).pack(side="right")
        self.position_label = ttk.Label(footer, textvariable=self.position, style="Muted.TLabel")
        self.sidebar_var = tk.BooleanVar(value=prefs.show_sidebar)
        self.minimap_var = tk.BooleanVar(value=prefs.show_minimap)
        self.coordinates_var = tk.BooleanVar(value=prefs.show_coordinates)
        self._apply_theme()
        self._apply_language()
        self._apply_visibility()
        root.bind("<Map>", lambda event: self._theme_window(root) if event.widget == root else None, add="+")
        root.bind("<Control-f>", self.focus_search)
        root.bind("<Control-comma>", lambda event: self.show_settings())
        root.bind("<F11>", lambda event: self.toggle_fullscreen())
        for widget in (root, self.canvas, self.listbox, self.entry):
            widget.bind("<Alt-Left>", lambda event: self.navigate_history(-1))
            widget.bind("<Alt-Right>", lambda event: self.navigate_history(1))
        root.bind("<Escape>", lambda event: self.toggle_fullscreen(False))
        self.entry.focus_set()

    def _apply_theme(self):
        self.theme_name = system_theme() if self.preferences.theme == "system" else self.preferences.theme
        c = self.colors = THEMES[self.theme_name]
        self.background = map_background(c)
        self.root.configure(background=c["app"])
        style = self.style
        font = "Microsoft JhengHei UI"
        style.configure(".", background=c["surface"], foreground=c["ink"], font=(font, 10),
                        bordercolor=c["line"], lightcolor=c["surface"], darkcolor=c["surface"], troughcolor=c["app"])
        style.configure("TFrame", background=c["surface"])
        style.configure("TLabel", background=c["surface"], foreground=c["ink"])
        style.configure("Muted.TLabel", foreground=c["muted"], font=(font, 9))
        style.configure("Error.TLabel", foreground=c["error"], font=(font, 9))
        style.configure("Section.TLabel", font=(font, 11, "bold"))
        for kind in ("TButton", "TMenubutton"):
            style.configure(kind, padding=(10, 7), width=0, background=c["surface"], foreground=c["ink"],
                            bordercolor=c["line"], focuscolor=c["accent"], arrowcolor=c["ink"], relief="flat")
            style.map(kind, background=[("pressed", c["selection"]), ("active", c["selection"])],
                      foreground=[("disabled", c["disabled"])], bordercolor=[("focus", c["accent"])])
        style.configure("Quiet.TButton", borderwidth=0)
        style.configure("Browse.TButton", padding=(5, 7), font=(font, 9))
        style.configure("Accent.TButton", background=c["accent"], foreground=c["on_accent"], bordercolor=c["accent"], font=(font, 9, "bold"))
        style.map("Accent.TButton", background=[("active", c["accent_hover"])], foreground=[("active", c["on_accent"])])
        style.configure("Zoom.TButton", font=("Segoe UI", 15), padding=(8, 2), width=2)
        for kind in ("TEntry", "TCombobox"):
            style.configure(kind, padding=8, background=c["surface"], fieldbackground=c["surface"], foreground=c["ink"], insertcolor=c["ink"],
                            selectbackground=c["selection"], selectforeground=c["ink"], arrowcolor=c["ink"])
            style.map(kind, fieldbackground=[("readonly", c["surface"])], foreground=[("readonly", c["ink"])],
                      background=[("active", c["selection"]), ("readonly", c["surface"])],
                      arrowcolor=[("!disabled", c["ink"])], bordercolor=[("focus", c["accent"])])
        style.configure("TCheckbutton", background=c["surface"], foreground=c["ink"], focuscolor=c["accent"],
                        indicatorbackground=c["surface"], indicatorforeground=c["ink"])
        style.map("TCheckbutton", background=[("active", c["surface"])], indicatorbackground=[("selected", c["accent"])])
        style.configure("TNotebook", background=c["surface"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", c["selection"]), ("!selected", c["surface"])], foreground=[("selected", c["ink"])])
        style.configure("Vertical.TScrollbar", background=c["line"], troughcolor=c["surface"], arrowcolor=c["ink"], borderwidth=0, arrowsize=12)
        self.root.option_add("*TCombobox*Listbox.background", c["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["selection"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["ink"])
        for widget, token in self.tinted:
            widget.configure(background=c[token])
        self.listbox.configure(background=c["surface"], foreground=c["muted"], selectbackground=c["selection"],
                               selectforeground=c["ink"], highlightbackground=c["surface"], highlightcolor=c["accent"])
        self.canvas.configure(background=c["map"], highlightbackground=c["map"], highlightcolor=c["accent"])
        self.canvas.itemconfigure("loading", fill=c["muted"])
        self.minimap.configure(background=c["map"])
        self.mark.configure(background=c["surface"])
        self.mark.delete("all")
        for points in ((2, 6, 10, 2, 10, 22, 2, 26), (10, 2, 18, 6, 18, 26, 10, 22), (18, 6, 24, 2, 24, 22, 18, 26)):
            self.mark.create_polygon(points, fill=c["selection"], outline=c["accent"], width=1.5)
        for menu in (self.view_menu, self.help_menu, self.tile_menu):
            menu.configure(background=c["surface"], foreground=c["ink"], activebackground=c["selection"],
                           activeforeground=c["ink"], disabledforeground=c["disabled"], font=(font, 10))
        self._request_paint()
        for window in (self.root, self.settings_window, self.info_window):
            if window is not None:
                self._theme_window(window)

    def _theme_window(self, window):
        if os.name != "nt":
            return
        # Official Win32 per-window title-bar setting; no OS preference changes.
        # https://learn.microsoft.com/windows/apps/desktop/modernize/apply-windows-themes
        try:
            from ctypes import wintypes
            ancestor = ctypes.windll.user32.GetAncestor
            ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
            ancestor.restype = wintypes.HWND
            window_id = ancestor(window.winfo_id(), 2)
            attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            dark = wintypes.BOOL(self.theme_name == "dark")
            attribute(window_id, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        except (AttributeError, OSError):
            pass

    def _apply_language(self):
        self.root.title(self.tr("app"))
        for widget, key in self.translated:
            widget.configure(text=self.tr(key))
        self.canvas.itemconfigure("loading", text=self.tr("loading"))
        self.match_text.set(self.tr("matches", count=len(self.filtered)))
        self._refresh_browser_labels()
        if self.selected is None:
            self.tile_name.set(self.tr("no_selection"))
        if self.search_error_key:
            self.search_error.set(self.tr(self.search_error_key))
        self._set_status(self.status_key, **self.status_values)
        self._apply_visibility()
        self._update_menus()

    def _refresh_browser_labels(self):
        self.row_combo.configure(values=(self.tr("all_tiles"), *(self.tr("row_option", row=row) for row in self.index.rows)))
        self.row_combo.current(0 if self.browser.row is None else self.index.rows.index(self.browser.row) + 1)
        self._sync_browser_selection()

    def clear_search(self, _event=None):
        if self.fullscreen:
            self.toggle_fullscreen(False)
        else:
            self.query.set("")
            self._flush_filter()
        return "break"

    def _update_menus(self):
        menu = self.view_menu
        menu.delete(0, "end")
        for key, direction, enabled, shortcut in (("previous_view", -1, self.history.can_back, "Alt+←"),
                                                   ("next_view", 1, self.history.can_forward, "Alt+→")):
            menu.add_command(label=self.tr(key), accelerator=shortcut,
                             command=lambda direction=direction: self.navigate_history(direction),
                             state="normal" if enabled else "disabled")
        menu.add_separator()
        for key, command, shortcut in (("fit_all", self.fit_all, "Home"), ("actual_size", self.actual_size, "1:1"),
                                        ("return_tile", self.locate_selected, ""), ("clear_selection", self.clear_selection, "")):
            menu.add_command(label=self.tr(key), command=command, accelerator=shortcut,
                             state="disabled" if key in ("return_tile", "clear_selection") and not self.selected else "normal")
        menu.add_separator()
        for key, name, variable in (("show_sidebar", "show_sidebar", self.sidebar_var), ("show_minimap", "show_minimap", self.minimap_var),
                                    ("show_coordinates", "show_coordinates", self.coordinates_var)):
            variable.set(getattr(self.preferences, name))
            menu.add_checkbutton(label=self.tr(key), variable=variable, command=lambda name=name, variable=variable: self.set_preference(name, variable.get()))
        menu.add_separator()
        menu.add_command(label=self.tr("exit_fullscreen" if self.fullscreen else "fullscreen"), accelerator="F11", command=self.toggle_fullscreen)
        self.help_menu.delete(0, "end")
        self.help_menu.add_command(label=self.tr("shortcuts"), command=lambda: self.show_info("shortcuts"))
        self.help_menu.add_command(label=self.tr("about"), command=lambda: self.show_info("about"))

    def _apply_visibility(self):
        if self.preferences.show_sidebar:
            if not self.sidebar.winfo_manager():
                self.sidebar.pack(side="left", fill="y", padx=(0, 1), before=self.map_area)
        else:
            self.sidebar.pack_forget()
        if self.preferences.show_coordinates:
            self.position_label.pack(side="right", padx=18)
        else:
            self.position_label.pack_forget()
        self.details_button.configure(text=("▾  " if self.preferences.show_details else "▸  ") + self.tr("details"))
        if self.preferences.show_details:
            self.details_content.pack(fill="x")
        else:
            self.details_content.pack_forget()
        self._place_minimap()

    def _place_minimap(self):
        width, height = self.dimensions()
        if self.preferences.show_minimap and width >= 720 and height >= 560:
            self.minimap_frame.place(relx=1, rely=1, x=-16, y=-16, anchor="se")
            self._update_minimap()
        else:
            self.minimap_frame.place_forget()

    def toggle_details(self):
        self.set_preference("show_details", not self.preferences.show_details)

    def set_preference(self, name, value):
        if getattr(self.preferences, name) == value:
            return
        setattr(self.preferences, name, value)
        if name == "theme":
            self._apply_theme()
        elif name == "language":
            self._apply_language()
        else:
            self._apply_visibility()
        self._update_menus()
        self._schedule_save()
        if self.settings_window is not None and name == "language":
            self._render_settings()
        elif self.settings_window is not None:
            for key, control in self.setting_controls.items():
                if hasattr(control, "preference_variable"):
                    control.preference_variable.set(getattr(self.preferences, key))
            self.setting_controls["theme"].current(("system", "light", "dark").index(self.preferences.theme))
        if self.info_window is not None and name == "language":
            self._render_info()

    def _schedule_save(self):
        if self.save_after is not None:
            self.root.after_cancel(self.save_after)
        self.save_after = self.root.after(200, self._save_preferences)

    def _save_preferences(self):
        self.save_after = None
        if not self.preferences.remember_view:
            self.preferences.last_tile = ""
            self.preferences.last_view = None
        try:
            self.settings_store.save(self.preferences)
        except OSError as error:
            self._set_status("settings_error", error=str(error))

    def focus_search(self, _event=None):
        self.set_preference("show_sidebar", True)
        self.entry.focus_set()
        self.entry.selection_range(0, "end")
        return "break"

    def toggle_fullscreen(self, enabled=None):
        enabled = not self.fullscreen if enabled is None else enabled
        if enabled == self.fullscreen:
            return
        if enabled:
            self.normal_size = self.root.winfo_width(), self.root.winfo_height()
        self.fullscreen = enabled
        self.root.attributes("-fullscreen", enabled)
        self._update_menus()

    def show_settings(self):
        if self.settings_window is None:
            window = self.settings_window = tk.Toplevel(self.root)
            window.transient(self.root)
            x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - 560) // 2)
            y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - 560) // 2)
            window.geometry(f"560x560+{x}+{y}")
            window.minsize(530, 530)
            window.resizable(False, False)
            window.protocol("WM_DELETE_WINDOW", self.close_settings)
            window.bind("<Escape>", lambda event: self.close_settings())
            self._render_settings()
            window.bind("<Map>", lambda event: self._theme_window(window) if event.widget == window else None, add="+")
        self.settings_window.lift()
        self.settings_window.focus_set()

    def _render_settings(self):
        window = self.settings_window
        selected = 0
        if hasattr(self, "settings_notebook") and self.settings_notebook.winfo_exists():
            selected = self.settings_notebook.index("current")
        for child in window.winfo_children():
            child.destroy()
        window.title(self.tr("settings"))
        self._theme_window(window)
        box = ttk.Frame(window, padding=18)
        box.pack(fill="both", expand=True)
        notebook = self.settings_notebook = ttk.Notebook(box)
        notebook.pack(fill="both", expand=True)
        general, performance = ttk.Frame(notebook, padding=20), ttk.Frame(notebook, padding=20)
        notebook.add(general, text=self.tr("general"))
        notebook.add(performance, text=self.tr("performance"))
        notebook.select(selected)
        self.setting_controls = {}
        for row, (name, key, codes, labels) in enumerate((
                ("language", "language", ("zh-TW", "en"), ("繁體中文", "English")),
                ("theme", "appearance", ("system", "light", "dark"), tuple(self.tr(k) for k in ("system", "light", "dark"))))):
            ttk.Label(general, text=self.tr(key)).grid(row=row, column=0, sticky="w", pady=(0, 14), padx=(0, 20))
            combo = ttk.Combobox(general, state="readonly", values=labels, width=23)
            combo.current(codes.index(getattr(self.preferences, name)))
            combo.grid(row=row, column=1, sticky="ew", pady=(0, 14))
            combo.bind("<<ComboboxSelected>>", lambda event, name=name, combo=combo, codes=codes: self.set_preference(name, codes[combo.current()]))
            self.setting_controls[name] = combo
        row = 2
        for title, fields in (("display", ("show_minimap", "show_coordinates")), ("startup", ("remember_view",))):
            ttk.Label(general, text=self.tr(title), style="Section.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 8))
            row += 1
            for name in fields:
                variable = tk.BooleanVar(value=getattr(self.preferences, name))
                control = ttk.Checkbutton(general, text=self.tr(name), variable=variable,
                                          command=lambda name=name, variable=variable: self.set_preference(name, variable.get()))
                control.preference_variable = variable
                control.grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
                self.setting_controls[name] = control
                row += 1
        general.columnconfigure(1, weight=1)
        ttk.Label(performance, text=self.tr("cache"), style="Section.TLabel").pack(anchor="w", pady=(0, 16))
        self.settings_cache_text = tk.StringVar()
        ttk.Label(performance, textvariable=self.settings_cache_text).pack(anchor="w", pady=4)
        ttk.Label(performance, text=self.tr("gdal_usage", limit=self.args.gdal_cache_mb)).pack(anchor="w", pady=4)
        for key in ("cache_note", "cache_note2"):
            ttk.Label(performance, text=self.tr(key), style="Muted.TLabel", wraplength=430, justify="left").pack(anchor="w", pady=(16, 0))
        ttk.Button(performance, text=self.tr("clear_cache"), command=self.clear_cache).pack(anchor="w", pady=20)
        bottom = ttk.Frame(box, padding=(0, 14, 0, 0))
        bottom.pack(fill="x")
        ttk.Label(bottom, text=self.tr("auto_saved"), style="Muted.TLabel").pack(side="left")
        ttk.Button(bottom, text=self.tr("close"), command=self.close_settings).pack(side="right")
        self._refresh_settings()

    def _refresh_settings(self):
        if self.settings_timer is not None:
            self.root.after_cancel(self.settings_timer)
        self.settings_timer = None
        if self.settings_window is not None:
            self.settings_cache_text.set(self.tr("cache_usage", used=self.worker.stats()["cache_bytes"] / 1048576, limit=self.args.cache_mb))
            self.settings_timer = self.root.after(1000, self._refresh_settings)

    def close_settings(self):
        if self.settings_timer is not None:
            self.root.after_cancel(self.settings_timer)
            self.settings_timer = None
        if self.settings_window is not None:
            self.settings_window.destroy()
            self.settings_window = None

    def show_info(self, kind):
        self.info_kind = kind
        if self.info_window is None:
            self.info_window = tk.Toplevel(self.root)
            self.info_window.transient(self.root)
            self.info_window.protocol("WM_DELETE_WINDOW", self.close_info)
            self.info_window.bind("<Escape>", lambda event: self.close_info())
            self.info_window.bind("<Map>", lambda event: self._theme_window(self.info_window) if event.widget == self.info_window else None, add="+")
        self._render_info()
        self.info_window.lift()

    def _render_info(self):
        for child in self.info_window.winfo_children():
            child.destroy()
        self.info_window.title(self.tr(self.info_kind))
        self._theme_window(self.info_window)
        box = ttk.Frame(self.info_window, padding=24)
        box.pack(fill="both", expand=True)
        text = self.tr("help_body" if self.info_kind == "shortcuts" else "about_body", count=len(self.index.tiles), width=self.index.width, height=self.index.height)
        if self.info_kind == "shortcuts":
            text += "\n\n" + self.tr("history_hint")
        else:
            text += f"\n\nVersion {__version__}\nApache-2.0 · See LICENSE and THIRD_PARTY_NOTICES.md"
        ttk.Label(box, text=text, wraplength=490, justify="left").pack(anchor="w")
        ttk.Button(box, text=self.tr("close"), command=self.close_info).pack(anchor="e", pady=(20, 0))

    def close_info(self):
        if self.info_window is not None:
            self.info_window.destroy()
            self.info_window = None

    def _check_system_theme(self):
        if self.preferences.theme == "system" and time.monotonic() >= self.next_system_check:
            self.next_system_check = time.monotonic() + 2
            if system_theme() != self.theme_name:
                self._apply_theme()
