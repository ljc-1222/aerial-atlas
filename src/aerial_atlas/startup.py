"""A quiet preparation screen; scan off-thread, build off-process, render only when ready."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
import uuid

from . import __version__
from .viewer_preferences import SettingsStore, THEMES, system_theme
from .datasets import DATASET_IDS, find_dataset, prepare_downloads, resolve_data_root
from .paths import output_dir

TEXT = {
    "en": {
        "title": "Your tiles, in context.", "intro": "Create a seamless map once. Explore every tile with its surroundings.",
        "source": "Source tiles", "output": "Local map", "checking": "Checking your map and source tiles…",
        "survey": "Survey", "choose": "Choose download folder", "importing": "Preparing downloaded imagery…",
        "build": "Create map", "rebuild": "Rebuild map", "check": "Check again", "folder": "Open images folder",
        "cancel": "Cancel", "cancelling": "Cancelling…", "existing": "Open existing map", "logs": "Open logs",
        "missing": "Download the survey files into images, or choose their download folder, then check again.", "ready": "{count:,} tiles · {width:,} × {height:,} pixels\nEstimated free space needed: {space:.1f} GiB",
        "changed": "Source files have changed. Rebuild to include them, or open the existing map.",
        "space": "Not enough disk space. Free space and check again.", "failed": "Could not create the map. {error}",
        "cancelled": "Map creation cancelled. You can try again when ready.",
        "inventory": "Checking source tiles", "overlaps": "Checking overlapping pixels", "mask": "Preparing coverage",
        "writing": "Writing the map", "overviews": "Creating zoom levels — this can take a few minutes",
        "verifying": "Verifying every source tile", "coverage": "Verifying coverage", "publishing": "Finishing your map",
        "complete": "Map ready", "note": "Your source images stay unchanged. No data is uploaded.",
    },
    "zh-TW": {
        "title": "從圖幅，看見全局。", "intro": "建立一次全圖，隨時查看每張圖幅與周圍環境。",
        "source": "來源圖幅", "output": "本機全圖", "checking": "正在檢查地圖與來源圖幅…",
        "survey": "航次", "choose": "選擇下載資料夾", "importing": "正在整理下載影像…",
        "build": "建立地圖", "rebuild": "重新建立", "check": "重新檢查", "folder": "開啟 images 資料夾",
        "cancel": "取消", "cancelling": "正在取消…", "existing": "開啟現有地圖", "logs": "開啟紀錄資料夾",
        "missing": "將航次檔案下載到 images，或選擇下載資料夾，再按重新檢查。", "ready": "{count:,} 張圖幅 · {width:,} × {height:,} 像素\n預估需要可用空間：{space:.1f} GiB",
        "changed": "來源檔案已變動。請重新建立以套用變更，或開啟現有地圖。",
        "space": "磁碟空間不足，請釋放空間後重新檢查。", "failed": "無法建立地圖。{error}",
        "cancelled": "已取消建立地圖。準備好後可以重試。",
        "inventory": "檢查來源圖幅", "overlaps": "驗證重疊像素", "mask": "準備覆蓋遮罩",
        "writing": "寫入全圖", "overviews": "建立縮圖層級，可能需要幾分鐘",
        "verifying": "逐張驗證圖幅", "coverage": "驗證覆蓋範圍", "publishing": "完成地圖",
        "complete": "地圖已就緒", "note": "來源影像保持原樣，資料不會上傳。",
    },
}


class Startup:
    def __init__(self, root, args, source, output, logs):
        self.root, self.args, self.source, self.output, self.logs = root, args, source, output, logs
        self.store = SettingsStore(args.settings_path)
        self.preferences = self.store.load()
        args.dataset = getattr(args, "dataset", None) or self.preferences.last_dataset
        args.switch_dataset = self.switch_dataset
        self.data_root = resolve_data_root(args.project_dir, getattr(args, "data_dir", None))
        self.auto_select = not getattr(args, "dataset_explicit", False)
        self.orthomosaic = None
        self.index_base = 1
        self.import_progress = None
        self.mailbox = Queue(maxsize=1)
        self.process = None
        self.killer = None
        self.kill_started = False
        self.cancel_deadline = None
        self.progress_file = self.cancel_file = None
        self.closing = self.opened = False
        self.scan_running = False
        self.info = None
        self.last_event = None
        self.layout_job = None
        self.message_key, self.message_values = "checking", {}
        root.title("Aerial Atlas")
        root.geometry("1040x720")
        root.minsize(760, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = self.style = ttk.Style(root)
        style.theme_use("clam")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        self.scroll = tk.Canvas(root, highlightthickness=0, borderwidth=0)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(root, orient="vertical", command=self.scroll.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.scrollbar.grid_remove()
        self.scroll.configure(yscrollcommand=self.scrollbar.set)
        outer = self.outer = ttk.Frame(self.scroll, padding=(42, 28))
        self.scroll_window = self.scroll.create_window((0, 0), window=outer, anchor="nw")
        self.scroll.bind("<Configure>", self.resize_content)
        outer.bind("<Configure>", self.scroll_bounds)
        root.bind("<MouseWheel>", self.scroll_wheel)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=f"Aerial Atlas  /  {__version__}", style="Brand.TLabel").pack(side="left")
        self.language = tk.StringVar(value=self.preferences.language)
        language = ttk.Combobox(header, textvariable=self.language, values=("en", "zh-TW"), state="readonly", width=8)
        language.pack(side="right")
        ttk.Label(header, text="Language", style="Muted.TLabel").pack(side="right", padx=(12, 6))
        language.bind("<<ComboboxSelected>>", self.update_appearance)
        self.theme = tk.StringVar(value=self.preferences.theme)
        theme = ttk.Combobox(header, textvariable=self.theme, values=("system", "light", "dark"), state="readonly", width=9)
        theme.pack(side="right", padx=10)
        ttk.Label(header, text="Appearance", style="Muted.TLabel").pack(side="right")
        theme.bind("<<ComboboxSelected>>", self.update_appearance)
        self.title = ttk.Label(outer, style="Title.TLabel")
        self.title.pack(anchor="w", pady=(38, 10))
        self.intro = ttk.Label(outer, style="Muted.TLabel", wraplength=850)
        self.intro.pack(anchor="w")
        surveys = ttk.Frame(outer)
        surveys.pack(fill="x", pady=(20, 0))
        self.survey_label = ttk.Label(surveys, style="Muted.TLabel")
        self.survey_label.pack(side="left", padx=(0, 10))
        labels = [f"{int(id[:3])+1911}-{id[3:5]}-{id[5:]}" for id in DATASET_IDS]
        self.survey_combo = ttk.Combobox(surveys, values=labels, state="readonly", width=14)
        self.survey_combo.current(DATASET_IDS.index(args.dataset))
        self.survey_combo.pack(side="left")
        self.survey_combo.bind("<<ComboboxSelected>>", self.select_dataset)
        self.data_button = ttk.Button(surveys, command=self.choose_data_folder)
        self.data_button.pack(side="right")
        self.locations = ttk.Frame(outer, padding=(20, 18), style="Panel.TFrame")
        self.locations.pack(fill="x", pady=(28, 22))
        self.source_label = ttk.Label(self.locations, style="PanelMuted.TLabel")
        self.source_label.pack(anchor="w")
        self.source_path = ttk.Label(self.locations, text=str(source), style="Path.TLabel", wraplength=860)
        self.source_path.pack(anchor="w", pady=(5, 15))
        self.output_label = ttk.Label(self.locations, style="PanelMuted.TLabel")
        self.output_label.pack(anchor="w")
        self.output_path = ttk.Label(self.locations, text=str(output), style="Path.TLabel", wraplength=860)
        self.output_path.pack(anchor="w", pady=(5, 0))
        self.status = ttk.Label(outer, wraplength=900, justify="left")
        self.status.pack(anchor="w", fill="x")
        self.bar = ttk.Progressbar(outer, mode="indeterminate")
        self.bar.pack(fill="x", pady=(18, 12))
        self.actions = ttk.Frame(outer)
        self.actions.pack(fill="x", pady=(8, 0))
        self.primary = ttk.Button(self.actions, style="Accent.TButton", command=self.start_build)
        self.primary.pack(side="left")
        self.secondary = ttk.Button(self.actions, command=self.scan)
        self.secondary.pack(side="left", padx=10)
        self.existing = ttk.Button(self.actions, command=lambda: self.open_map(self.info))
        self.folder = ttk.Button(self.actions, command=self.open_images)
        self.folder.pack(side="right")
        self.footer = ttk.Label(outer, style="Muted.TLabel")
        self.footer.pack(side="bottom", anchor="w", pady=(22, 0))
        root.bind("<Configure>", self.resize)
        self.update_appearance(save=False)
        self.scan()
        self.poll_job = root.after(100, self.poll)

    def tr(self, key, **values):
        return TEXT[self.preferences.language][key].format(**values)

    def resize(self, event):
        if event.widget == self.root:
            width = max(300, event.width-125)
            for label in (self.status, self.source_path, self.output_path, self.intro):
                label.configure(wraplength=width)
            self.request_layout()

    def request_layout(self):
        if self.layout_job is None:
            # Text wrapping changes requested height after Tk's idle geometry pass.
            self.layout_job = self.root.after(30, self.finish_layout)

    def finish_layout(self):
        self.layout_job = None
        if not self.opened:
            self.scroll_bounds()

    def resize_content(self, event):
        self.scroll.itemconfigure(self.scroll_window, width=event.width)
        self.scroll_bounds()

    def scroll_bounds(self, event=None):
        height=max(self.outer.winfo_reqheight(),self.scroll.winfo_height())
        self.scroll.itemconfigure(self.scroll_window, height=height)
        self.scroll.configure(scrollregion=self.scroll.bbox("all"))
        if self.outer.winfo_reqheight()>self.scroll.winfo_height()+2:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()

    def scroll_wheel(self, event):
        if self.outer.winfo_reqheight()>self.scroll.winfo_height():
            self.scroll.yview_scroll(-int(event.delta/120), "units")

    def update_appearance(self, event=None, save=True):
        self.preferences.language, self.preferences.theme = self.language.get(), self.theme.get()
        theme = system_theme() if self.preferences.theme == "system" else self.preferences.theme
        colors = THEMES[theme]
        self.root.configure(background=colors["app"])
        self.scroll.configure(background=colors["app"])
        for name, options in {
            "TFrame": dict(background=colors["app"]),
            "TLabel": dict(background=colors["app"], foreground=colors["ink"], font=("Segoe UI", 11)),
            "Muted.TLabel": dict(foreground=colors["muted"]),
            "Brand.TLabel": dict(font=("Segoe UI", 12, "bold")),
            "Title.TLabel": dict(font=("Segoe UI", 28, "bold")),
            "Panel.TFrame": dict(background=colors["surface"]),
            "PanelMuted.TLabel": dict(background=colors["surface"], foreground=colors["muted"], font=("Segoe UI", 10)),
            "Path.TLabel": dict(background=colors["surface"], font=("Segoe UI", 10)),
            "TButton": dict(padding=(14, 10), background=colors["surface"], foreground=colors["ink"]),
            "Accent.TButton": dict(background=colors["accent"], foreground=colors["on_accent"]),
            "TCombobox": dict(fieldbackground=colors["surface"], foreground=colors["ink"]),
            "TProgressbar": dict(background=colors["accent"], troughcolor=colors["line"]),
        }.items():
            self.style.configure(name, **options)
        self.style.map("Accent.TButton", background=[("active", colors["accent_hover"])])
        self.style.map("TButton", foreground=[("disabled", colors["disabled"])])
        self.style.map("TCombobox", fieldbackground=[("readonly", colors["surface"])],
                       foreground=[("readonly", colors["ink"])],
                       selectbackground=[("readonly", colors["surface"])],
                       selectforeground=[("readonly", colors["ink"])])
        self.title.configure(text=self.tr("title"))
        self.intro.configure(text=self.tr("intro"))
        self.source_label.configure(text=self.tr("source"))
        self.output_label.configure(text=self.tr("output"))
        self.footer.configure(text=self.tr("note"))
        self.folder.configure(text=self.tr("folder"))
        self.survey_label.configure(text=self.tr("survey"))
        self.data_button.configure(text=self.tr("choose"))
        self.existing.configure(text=self.tr("existing"))
        self.primary.configure(text=self.tr("rebuild" if self.info else "build"))
        self.secondary.configure(text=self.tr("cancel" if self.process else "check"))
        self.show(self.message_key, **self.message_values)
        if save:
            try:
                self.store.save(self.preferences)
            except OSError:
                logging.exception("Could not save preparation preferences")

    def show(self, key, **values):
        self.message_key, self.message_values = key, values
        self.status.configure(text=self.tr(key, **values) if key in TEXT["en"] else key)
        self.request_layout()

    def select_dataset(self, _event=None):
        if self.scan_running or self.process:
            return
        self.args.dataset = DATASET_IDS[self.survey_combo.current()]
        self.args.source = self.args.image = self.args.index = self.args.tile = None
        self.auto_select = False
        self.info = None
        self.scan()

    def choose_data_folder(self):
        if self.scan_running or self.process:
            return
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.data_root, title=self.tr("choose"))
        if folder:
            self.args.data_dir = self.data_root = Path(folder).resolve()
            self.args.source = self.args.image = self.args.index = self.args.tile = None
            self.auto_select = True
            self.info = None
            self.scan()

    def scan(self):
        if self.scan_running or self.process:
            return
        self.scan_running = True
        self.survey_combo.configure(state="disabled")
        self.data_button.state(["disabled"])
        self.primary.state(["disabled"])
        self.secondary.state(["disabled"])
        self.existing.pack_forget()
        self.show("checking")
        self.bar.configure(mode="indeterminate")
        self.bar.pack(fill="x", pady=(18, 12), before=self.actions)
        self.bar.start(15)

        def inspect():
            from .mosaic import current_map, preflight, BuildCancelled
            info = None
            issue = None
            prepared = bool(self.args.image)

            def importing(stage, done, total, message):
                if self.closing:
                    raise BuildCancelled("Download preparation cancelled.")
                self.import_progress = (done, total) if done < total else None

            try:
                if not self.args.image:
                    prepare_downloads(self.data_root, importing)
                    dataset = find_dataset(self.data_root, self.args.dataset)
                    if (self.auto_select and not self.args.source and not dataset.tiles.is_dir()
                            and not (output_dir(self.args.project_dir, dataset.id) / "current.json").exists()):
                        for id in DATASET_IDS:
                            candidate = find_dataset(self.data_root, id)
                            if candidate.tiles.is_dir():
                                dataset = candidate
                                self.args.dataset = id
                                break
                    self.auto_select = False
                    self.source = (self.args.source or dataset.tiles).resolve()
                    self.output = output_dir(self.args.project_dir, self.args.dataset)
                    self.orthomosaic, self.index_base = dataset.image, dataset.index_base
                prepared = True
                if self.args.image:
                    from .raster import inspect_raster
                    info = dict(image=self.args.image.resolve(strict=True), index=self.args.index.resolve(strict=True),
                                changed=False, extent=inspect_raster(self.args.image))
                elif (self.output / "current.json").exists():
                    info = current_map(self.output, self.source, image=self.orthomosaic)
                if info and not info["changed"]:
                    self.mailbox.put(("open", info))
                    return
            except Exception as error:
                issue = str(error)
                if self.closing or not prepared:
                    self.mailbox.put(("error", (None, issue)))
                    return
            try:
                if self.index_base == 0 and self.orthomosaic is None:
                    raise FileNotFoundError(f"Download {self.args.dataset}_input_orthomosaic-001.tif together with its tile ZIPs.")
                if issue and not self.source.is_dir():
                    raise FileNotFoundError(issue)
                ready = preflight(self.source, self.output, index_base=self.index_base, image=self.orthomosaic)
                self.mailbox.put(("ready", (info, ready, issue)))
            except Exception as error:
                kind = "missing" if not self.source.is_dir() and issue is None else "error"
                self.mailbox.put((kind, (info, str(error) if issue is None else issue+"\n\n"+str(error))))

        threading.Thread(target=inspect, name="atlas-preflight", daemon=True).start()

    def start_build(self):
        if self.process or self.scan_running:
            return
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            job = uuid.uuid4().hex
            self.progress_file = self.output / (".progress-"+job+".json")
            self.cancel_file = self.output / (".cancel-"+job)
            command = ([sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "aerial_atlas"])
            command += ["--build", "--project-dir", str(self.args.project_dir), "--source", str(self.source),
                        "--dataset", self.args.dataset, "--data-dir", str(self.data_root),
                        "--progress-file", str(self.progress_file), "--cancel-file", str(self.cancel_file)]
            options = dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == "nt" else {}
            self.killer = None
            self.kill_started = False
            self.cancel_deadline = None
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, **options)
            self.primary.state(["disabled"])
            self.survey_combo.configure(state="disabled")
            self.data_button.state(["disabled"])
            self.existing.pack_forget()
            self.secondary.configure(text=self.tr("cancel"), command=self.cancel)
            self.last_event = None
            self.show("inventory")
            self.bar.configure(mode="indeterminate")
            self.bar.pack(fill="x", pady=(18, 12), before=self.actions)
            self.bar.start(15)
        except OSError as error:
            self.show("failed", error=str(error))

    def cancel(self):
        if not self.process:
            return
        if self.cancel_deadline is None:
            self.cancel_file.touch()
            self.cancel_deadline = time.monotonic()+1.5
        self.secondary.state(["disabled"])
        self.show("cancelling")

    def poll(self):
        self.poll_job = None
        if self.opened:
            return
        try:
            kind, value = self.mailbox.get_nowait()
            self.scan_running = False
            self.survey_combo.current(DATASET_IDS.index(self.args.dataset))
            self.survey_combo.configure(state="disabled" if self.args.image else "readonly")
            self.data_button.state(["!disabled"])
            self.source_path.configure(text=str(self.source))
            self.output_path.configure(text=str(self.output))
            if self.closing:
                self._destroy()
                return
            self.bar.stop()
            self.bar.pack_forget()
            self.secondary.state(["!disabled"])
            if kind == "open":
                self.open_map(value)
                return
            if kind == "ready":
                self.info, ready, issue = value
                extent = ready["extent"]
                self.show("ready", count=ready["count"], width=extent["width"], height=extent["height"], space=ready["required_bytes"]/1024**3)
                if self.info:
                    self.show("changed")
                elif issue:
                    self.show("failed", error=issue)
                if ready["enough_space"]:
                    self.primary.state(["!disabled"])
                else:
                    self.show("space")
            else:
                self.info, issue = value
                self.show("missing" if kind == "missing" else "failed", error=issue)
            self.primary.configure(text=self.tr("rebuild" if self.info else "build"))
            if self.info:
                self.existing.pack(side="left", padx=4)
        except Empty:
            pass
        if self.scan_running and self.import_progress is not None:
            done, total = self.import_progress
            self.show("importing")
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=total, value=done)
        if self.process:
            try:
                event = json.loads(self.progress_file.read_text(encoding="utf-8"))
                if event != self.last_event and not self.cancel_file.exists():
                    self.last_event = event
                    stage = event["stage"]
                    if stage in ("failed", "cancelled"):
                        self.show(stage, error=event.get("message", ""))
                    else:
                        self.show(stage)
                    self.bar.stop()
                    if event.get("total", 0) > 0:
                        self.bar.configure(mode="determinate", maximum=event["total"], value=event["done"])
                    else:
                        self.bar.configure(mode="indeterminate")
                        self.bar.start(15)
            except (OSError, ValueError, KeyError):
                pass
            if (self.cancel_deadline is not None and self.process.poll() is None
                and time.monotonic() >= self.cancel_deadline and not self.kill_started):
                self.kill_started = True
                if os.name == "nt":
                    # A venv python.exe can be a redirector with a separate real
                    # worker. Kill its tree once, without waiting on Tk's thread.
                    try:
                        self.killer = subprocess.Popen(
                            [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                             "/PID", str(self.process.pid), "/T", "/F"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    except OSError as error:
                        logging.exception("Could not stop map worker process tree")
                        self.show("failed", error=str(error))
                else:
                    self.process.terminate()
            code = self.process.poll()
            killer_done = self.killer is None or self.killer.poll() is not None
            if code is not None and killer_done:
                if self.killer is not None:
                    self.killer.wait()  # poll above confirmed this cannot block.
                    self.killer = None
                cancelled = self.cancel_file.exists()
                self.process.wait()
                self.process = None
                self.bar.stop()
                self.progress_file.unlink(missing_ok=True)
                self.cancel_file.unlink(missing_ok=True)
                self.secondary.configure(text=self.tr("check"), command=self.scan)
                self.secondary.state(["!disabled"])
                self.survey_combo.configure(state="readonly")
                self.data_button.state(["!disabled"])
                if self.closing:
                    self._destroy()
                    return
                if code == 0:
                    self.scan()
                else:
                    self.primary.state(["!disabled"])
                    if cancelled:
                        self.show("cancelled")
                    elif not self.last_event or self.last_event.get("stage") != "failed":
                        self.show("failed", error=f"Worker exited ({code}). See {self.logs} for details.")
        if self.closing and not self.process and not self.scan_running:
            self._destroy()
            return
        self.poll_job = self.root.after(100, self.poll)

    def open_map(self, info):
        from .mosaic_viewer import MosaicViewer, TileIndex
        try:
            extent = info.get("extent") or info["manifest"]["extent"]
            index = TileIndex(info["index"], extent=(extent["width"], extent["height"]))
            # Content identity survives rebuilding, moving or updating the application folder.
            self.args.map_identity = "aerial-atlas:v1:" + info["manifest"]["index_sha256"] if "manifest" in info else str(info["image"].resolve()).casefold()
            if self.args.image:
                self.args.map_identity = str(info["image"].resolve()).casefold()
            from .viewer_preferences import migrate_view_identity
            saved = self.preferences.dataset_views.get(self.args.dataset)
            if saved:
                self.preferences.last_view = saved
            elif self.preferences.last_dataset != self.args.dataset:
                self.preferences.last_view = None
                self.preferences.last_tile = ""
            self.args.restored_view = migrate_view_identity(self.preferences, info["index"], self.args.map_identity,
                                                           [index.width, index.height])
            self.opened = True
            if self.layout_job:
                self.root.after_cancel(self.layout_job)
                self.layout_job = None
            if self.poll_job:
                self.root.after_cancel(self.poll_job)
            self.root.unbind("<Configure>")
            self.root.unbind("<MouseWheel>")
            self.bar.stop()
            self.outer.destroy()
            self.scroll.destroy()
            self.scrollbar.destroy()
            self.viewer = MosaicViewer(self.root, index, info["image"], self.args)
        except Exception as error:
            logging.exception("Could not open viewer")
            from tkinter import messagebox
            messagebox.showerror("Aerial Atlas", str(error), parent=self.root)
            self._destroy()

    def open_images(self):
        folder = self.data_root
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)

    def switch_dataset(self, id):
        if id == self.args.dataset:
            return
        self.viewer.close(destroy_root=False)
        self.args.dataset = id
        self.args.dataset_explicit = True
        self.args.source = self.args.image = self.args.index = self.args.tile = None
        self.args.restored_view = None
        self.root.attributes("-fullscreen", False)
        for binding in self.root.bind():
            self.root.unbind(binding)
        for child in self.root.winfo_children():
            child.destroy()

        def reopen():
            if self.viewer.worker.is_alive():
                self.root.after(25, reopen)
                return
            Startup(self.root, self.args, self.data_root / id / "tiles_png_1024",
                    output_dir(self.args.project_dir, id), self.logs)

        reopen()

    def close(self):
        self.closing = True
        if self.process:
            self.cancel()
        elif not self.scan_running:
            self._destroy()

    def _destroy(self):
        if self.bar.winfo_exists():
            self.bar.stop()
        for name in ("poll_job", "layout_job"):
            job = getattr(self, name)
            if job:
                self.root.after_cancel(job)
                setattr(self, name, None)
        self.root.destroy()
