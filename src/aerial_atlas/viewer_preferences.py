"""Small, validated user preferences and semantic UI resources (stdlib only)."""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
import csv
from .viewer_navigation import validated_saved_view
from .datasets import DATASET_IDS


THEMES = {
    "light": {
        "surface": "#FFFFFF", "app": "#F3F6F4", "ink": "#20372D",
        "muted": "#52685C", "accent": "#216C51", "accent_hover": "#18563F",
        "line": "#C8D5CC", "selection": "#E7F2EB", "map": "#E8EEEA",
        "locator": "#F5AA4C", "error": "#A73835", "on_accent": "#FFFFFF",
        "halo": "#20372D", "disabled": "#758078",
    },
    "dark": {
        "surface": "#202C29", "app": "#17221F", "ink": "#E6EEE9",
        "muted": "#AABDB2", "accent": "#94CDB0", "accent_hover": "#B3DEC7",
        "line": "#4C6256", "selection": "#334D40", "map": "#18231F",
        "locator": "#F5AA4C", "error": "#FFADA5", "on_accent": "#17382A",
        "halo": "#162A20", "disabled": "#82948A",
    },
}

STRINGS = {
    "zh-TW": {
        "app": "航測圖台", "view": "檢視", "settings": "設定", "help": "說明", "survey": "航次",
        "survey_prepared": "已建立", "survey_available": "已下載", "survey_missing": "缺少資料",
        "survey_invalid": "請檢查檔案", "switching_survey": "正在開啟 {date}…",
        "search": "圖幅瀏覽", "search_hint": "搜尋編號或檔名，例如 30_35", "locate": "定位",
        "tile_ids": "編號", "row_filter": "依列瀏覽", "all_tiles": "全部圖幅",
        "row_option": "第 {row:02} 列", "previous_tile": "‹ 上一張", "next_tile": "下一張 ›",
        "no_browse_results": "沒有符合的編號\n選擇「全部圖幅」可重新瀏覽",
        "tiles_here": "此處圖幅（{count}）", "no_tiles_here": "此處沒有圖幅",
        "tiles": "圖幅", "matches": "{count:,}", "details": "圖幅資訊",
        "no_selection": "尚未選取圖幅", "grid": "列／欄", "origin": "左上角",
        "size": "圖幅尺寸", "center": "中心座標", "minimap": "全圖導覽",
        "fit_all": "顯示全圖", "actual_size": "原始像素 1:1", "return_tile": "回到圖幅",
        "previous_view": "上一個視野", "next_view": "下一個視野",
        "history_hint": "Alt+← / Alt+→：上一個／下一個視野，保留當時的位置與縮放。",
        "clear_selection": "清除外框", "show_sidebar": "顯示搜尋面板",
        "show_minimap": "顯示全圖導覽", "show_coordinates": "顯示游標座標",
        "zoom_in": "放大", "zoom_out": "縮小", "ready": "就緒",
        "preparing": "正在準備地圖…", "loading": "正在準備正射影像…",
        "reading": "正在載入細節…", "empty_query": "請輸入圖檔名稱或列_欄。",
        "not_found": "查無圖幅，請確認名稱或從清單選擇。",
        "ambiguous": "有多個符合項目，請選擇完整圖檔名稱。",
        "select_first": "先輸入名稱或從清單選擇圖幅。", "map_error": "無法讀取地圖",
        "detail_error": "細節讀取失敗，保留預覽：{error}",
        "clearing": "正在清除快取…", "cleared": "快取已清除",
        "general": "一般", "performance": "效能", "language": "語言",
        "appearance": "外觀", "system": "跟隨系統", "light": "淺色", "dark": "深色",
        "display": "地圖顯示", "startup": "啟動",
        "remember_view": "恢復上次視野與視窗大小", "auto_saved": "變更立即生效並自動儲存",
        "close": "關閉", "cache": "記憶體快取", "clear_cache": "清除快取",
        "cache_usage": "影像快取  {used:.1f} / {limit} MiB",
        "gdal_usage": "解碼快取上限  {limit} MiB",
        "cache_note": "快取會自動淘汰舊內容，通常不需手動清除。清除後仍保留目前畫面。",
        "cache_note2": "此上限僅計算快取；整個程式另有影像與顯示記憶體。",
        "settings_error": "設定無法儲存：{error}", "shortcuts": "操作說明", "about": "關於航測圖台",
        "help_body": "從左側編號清單點選，或用「依列瀏覽」縮小範圍。\n上一張／下一張可依目前清單逐張查看。\n也可搜尋檔名或列_欄（如 30_35），按 Enter 定位。\n每次定位保留圖幅周圍 1.5 倍視野。\n\n地圖按右鍵／Shift+F10　選擇此處圖幅（含重疊圖幅）\nPageUp / PageDown　上一張／下一張\n清單方向鍵移動焦點，Enter 定位；Esc 清空搜尋\n滑鼠拖曳　平移；滾輪 / + / −　縮放\nHome　顯示全圖；地圖方向鍵　平移\nCtrl+F　搜尋；Ctrl+,　設定\nF11　全螢幕；Esc 返回\n\n座標為主圖像素，左上角為 (0, 0)，X 向右、Y 向下。",
        "about_body": "航測圖台\n本機正射影像檢視器\n\n{count:,} 張圖幅　{width:,} × {height:,} 像素\n\n主圖以唯讀方式開啟。\n定位外框不會寫入影像。",
        "fullscreen": "全螢幕", "exit_fullscreen": "離開全螢幕",
    },
    "en": {
        "app": "Aerial Atlas", "view": "View", "settings": "Settings", "help": "Help", "survey": "Survey",
        "survey_prepared": "Prepared", "survey_available": "Downloaded", "survey_missing": "Missing files",
        "survey_invalid": "Check files", "switching_survey": "Opening {date}…",
        "search": "Browse tiles", "search_hint": "Search ID or filename, e.g. 30_35", "locate": "Locate",
        "tile_ids": "Tile ID", "row_filter": "Browse row", "all_tiles": "All tiles",
        "row_option": "Row {row:02}", "previous_tile": "‹ Previous", "next_tile": "Next ›",
        "no_browse_results": "No matching IDs\nChoose All tiles to browse again",
        "tiles_here": "Tiles here ({count})", "no_tiles_here": "No tiles here",
        "tiles": "Tiles", "matches": "{count:,}", "details": "Tile information",
        "no_selection": "No tile selected", "grid": "Row / column", "origin": "Top left",
        "size": "Tile size", "center": "Center", "minimap": "Overview",
        "fit_all": "Fit map", "actual_size": "Actual pixels 1:1", "return_tile": "Return to tile",
        "previous_view": "Previous view", "next_view": "Next view",
        "history_hint": "Alt+Left / Alt+Right: previous / next view, preserving its position and zoom.",
        "clear_selection": "Clear outline", "show_sidebar": "Show search panel",
        "show_minimap": "Show overview", "show_coordinates": "Show cursor coordinates",
        "zoom_in": "Zoom in", "zoom_out": "Zoom out", "ready": "Ready",
        "preparing": "Preparing map…", "loading": "Preparing orthophoto…",
        "reading": "Loading details…", "empty_query": "Enter a filename or row_column.",
        "not_found": "Tile not found. Check the name or choose from the list.",
        "ambiguous": "Multiple matches. Choose a complete filename.",
        "select_first": "Enter a name or choose a tile first.", "map_error": "Cannot read map",
        "detail_error": "Cannot load details; keeping preview: {error}",
        "clearing": "Clearing cache…", "cleared": "Cache cleared",
        "general": "General", "performance": "Performance", "language": "Language",
        "appearance": "Appearance", "system": "Follow system", "light": "Light", "dark": "Dark",
        "display": "Map display", "startup": "Startup",
        "remember_view": "Restore last view and window size", "auto_saved": "Changes apply and save automatically",
        "close": "Close", "cache": "Memory cache", "clear_cache": "Clear cache",
        "cache_usage": "Image cache  {used:.1f} / {limit} MiB",
        "gdal_usage": "Decoder cache limit  {limit} MiB",
        "cache_note": "Old entries are removed automatically. Manual clearing is usually unnecessary and keeps the current view.",
        "cache_note2": "These limits cover caches. Image buffers and display memory are additional.",
        "settings_error": "Could not save settings: {error}", "shortcuts": "How to use", "about": "About Aerial Atlas",
        "help_body": "Click an ID in the list, or narrow it with Browse row.\nPrevious / Next steps through the current list.\nYou can also search a filename or row_column (e.g. 30_35) and press Enter.\nEvery selection uses 1.5× context.\n\nRight-click map / Shift+F10   Choose a tile here, including overlaps\nPageUp / PageDown   Previous / Next tile\nList arrows move focus; Enter locates; Esc clears search\nDrag   Pan; wheel / + / −   Zoom\nHome   Fit map; map arrow keys   Pan\nCtrl+F   Search; Ctrl+,   Settings\nF11   Full screen; Esc to exit\n\nCoordinates are map pixels: (0, 0) at top left, X right, Y down.",
        "about_body": "Aerial Atlas\nLocal orthophoto viewer\n\n{count:,} tiles   {width:,} × {height:,} pixels\n\nMaps are opened read-only.\nSelection outlines do not modify images.",
        "fullscreen": "Full screen", "exit_fullscreen": "Exit full screen",
    },
}


@dataclass
class Preferences:
    language: str = "en"
    theme: str = "system"
    show_minimap: bool = True
    show_coordinates: bool = False
    show_details: bool = False
    show_sidebar: bool = True
    remember_view: bool = True
    last_tile: str = ""
    last_view: dict | None = None
    last_dataset: str = "1150427"
    dataset_views: dict = field(default_factory=dict)
    window_width: int = 1480
    window_height: int = 940


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config")) / "UAVMapViewer" / "settings.json"

    def load(self) -> Preferences:
        prefs = Preferences()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return prefs
        if not isinstance(data, dict):
            return prefs
        for key, allowed in (("language", STRINGS), ("theme", ("system", "light", "dark"))):
            if isinstance(data.get(key), str) and data[key] in allowed:
                setattr(prefs, key, data[key])
        for key in ("show_minimap", "show_coordinates", "show_details", "show_sidebar", "remember_view"):
            if type(data.get(key)) is bool:
                setattr(prefs, key, data[key])
        for key, minimum in (("window_width", 960), ("window_height", 640)):
            if type(data.get(key)) is int and minimum <= data[key] <= 7680:
                setattr(prefs, key, data[key])
        if isinstance(data.get("last_tile"), str) and len(data["last_tile"]) < 256:
            prefs.last_tile = data["last_tile"]
        prefs.last_view = validated_saved_view(data.get("last_view"))
        if data.get("last_dataset") in DATASET_IDS:
            prefs.last_dataset = data["last_dataset"]
        if isinstance(data.get("dataset_views"), dict):
            for id in DATASET_IDS:
                view = validated_saved_view(data["dataset_views"].get(id))
                if view:
                    prefs.dataset_views[id] = view
        return prefs

    def save(self, prefs: Preferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix="settings-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(prefs), stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def migrate_view_identity(prefs: Preferences, index_path: Path, identity: str, extent: list[int]):
    """Read-only migration of earlier path-based views, only with identical source hashes."""
    saved = prefs.last_view
    if not saved or saved["map_key"] == identity or saved["extent"] != extent:
        return None
    try:
        old_index = Path(saved["map_key"]).with_name("tile_index.csv")
        with old_index.open(encoding="utf-8-sig", newline="") as stream:
            previous = list(csv.DictReader(stream))
        with index_path.open(encoding="utf-8-sig", newline="") as stream:
            current = list(csv.DictReader(stream))
        if current and previous == current and all(len(row.get("source_file_sha256", "")) == 64 for row in current):
            return dict(saved, map_key=identity)
    except (OSError, ValueError, KeyError):
        pass
    return None


def system_theme() -> str:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    return "light"


def map_background(colors: dict) -> tuple[int, int, int, int]:
    value = colors["map"].lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
