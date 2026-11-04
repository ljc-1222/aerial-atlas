"""Interactive Windows smoke/regression checks using synthetic inputs only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk

from PIL import ImageGrab
from aerial_atlas.app import parser
from aerial_atlas.demo import create_demo
from aerial_atlas.paths import output_dir, source_dir, atomic_json
from aerial_atlas.startup import Startup
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def pump(root,predicate,seconds=30):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        root.update()
        if predicate(): return
        time.sleep(.02)
    raise TimeoutError("GUI condition did not complete")


def capture(root,path):
    import ctypes
    root.lift()
    root.update_idletasks()
    time.sleep(.2)
    ctypes.windll.user32.GetAncestor.argtypes=[ctypes.c_void_p,ctypes.c_uint]
    ctypes.windll.user32.GetAncestor.restype=ctypes.c_void_p
    hwnd=ctypes.windll.user32.GetAncestor(root.winfo_id(),2)
    for attempt in range(3):
        try:
            ImageGrab.grab(window=hwnd).save(path)
            return
        except OSError:
            if attempt==2: raise
            time.sleep(.1)


def check_survey_picker(viewer):
    from aerial_atlas.viewer_preferences import THEMES
    root, picker = viewer.root, viewer.survey_picker
    original = viewer.preferences.language, viewer.preferences.theme, root.geometry()
    selected, command = picker.variable.get(), picker.command
    calls = []
    try:
        picker.command = calls.append
        root.geometry("960x640")
        for language in ("en", "zh-TW"):
            for theme in ("light", "dark"):
                viewer.preferences.language, viewer.preferences.theme = language, theme
                viewer._apply_theme()
                viewer._apply_language()
                root.update()
                assert picker.buttons[selected].instate(["selected"])
                for button in picker.buttons.values():
                    assert button.winfo_class() == "TRadiobutton"
                    assert button.winfo_width() >= button.winfo_reqwidth(), "Survey button clipped"
                    assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
                assert viewer.style.lookup("Survey.TRadiobutton", "background", ("selected",)) == THEMES[theme]["accent"]
        button = picker.buttons[selected]
        button.focus_force()
        root.update()
        button.event_generate("<Right>")
        root.update()
        assert calls and calls[-1] != selected, "Arrow key did not select the next survey"
        picker.buttons[picker.variable.get()].event_generate("<Return>")
        root.update()
        assert len(calls) == 2, "Enter did not activate the focused survey"
        picker.select("1150427")
        picker.buttons["1150806"].focus_force()
        root.update()
        picker.buttons["1150806"].event_generate("<Right>")
        root.update()
        assert len(calls) == 3 and calls[-1] == "1150427", "Arrow key ignored the focused date"
        picker.buttons["1150427"].event_generate("<KeyPress-space>")
        picker.buttons["1150427"].event_generate("<KeyRelease-space>")
        root.update()
        assert len(calls) == 4, "Space did not activate the focused survey"
        picker.set_busy(True)
        for button in picker.buttons.values():
            button.invoke()
        assert len(calls) == 4, "Busy survey selector accepted input"
    finally:
        picker.command = command
        picker.select(selected)
        picker.set_busy(False)
        viewer.preferences.language, viewer.preferences.theme = original[:2]
        viewer._apply_theme()
        viewer._apply_language()
        root.geometry(original[2])
        root.update()
        viewer.entry.focus_set()


def check_surveys(project, errors):
    sys.path.insert(0, str(ROOT / "tests"))
    from support import survey_downloads
    for id in ("1150618", "1150806"):
        survey_downloads(project, id)
    args = parser().parse_args(["--project-dir", str(project), "--settings-path", str(project / "settings.json"),
                                "--dataset", "1150427"])
    root = tk.Tk()
    root.report_callback_exception = lambda *error: errors.append(str(error))
    app = Startup(root, args, source_dir(project), output_dir(project), project / "logs")

    def switch(id):
        previous = args.switch_dataset.__self__
        previous.viewer.survey_picker.buttons[id].invoke()
        pump(root, lambda: args.switch_dataset.__self__ is not previous)
        current = args.switch_dataset.__self__
        pump(root, lambda: not current.scan_running)
        if not current.opened:
            assert current.message_key == "ready", current.message_values
            current.start_build()
        pump(root, lambda: current.opened and current.viewer.detail_updates > 0)
        assert previous.viewer.closed and not previous.viewer.worker.is_alive()
        return current.viewer

    try:
        pump(root, lambda: app.opened and app.viewer.detail_updates > 0)
        check_survey_picker(app.viewer)
        app.viewer.locate_text("2_2")
        april = app.viewer.capture_view()
        june = switch("1150618")
        assert (june.index.width, june.index.height) == (1536, 1280)
        june.locate_text("0_1")
        june.row_combo.current(1)
        june._row_changed()
        expected = june.capture_view()
        august = switch("1150806")
        assert august.selected is None, "Selection leaked between surveys"
        august.locate_text("0_0")
        restored = switch("1150618")
        pump(root, lambda: restored.selected is not None)
        assert restored.capture_view().equivalent(expected)
        restored = switch("1150427")
        pump(root, lambda: restored.selected is not None)
        assert restored.capture_view().equivalent(april)
    finally:
        try:
            active = args.switch_dataset.__self__
            if active.opened:
                active.viewer.close()
            else:
                active.close()
                pump(root, lambda: not active.scan_running)
        except tk.TclError:
            pass


def check_close_during_survey_switch(project, errors):
    args = parser().parse_args(["--project-dir", str(project), "--dataset", "1150427",
                                "--settings-path", str(project / "settings.json")])
    root = tk.Tk()
    root.report_callback_exception = lambda *error: errors.append(str(error))
    app = Startup(root, args, source_dir(project), output_dir(project), project / "logs")
    released, closed = threading.Event(), threading.Event()
    try:
        pump(root, lambda: app.opened and app.viewer.detail_updates > 0)
        worker = app.viewer.worker
        alive = worker.is_alive
        with patch.object(worker, "is_alive", side_effect=lambda: not released.is_set() or alive()):
            app.viewer.survey_picker.buttons["1150618"].invoke()
            root.update()
            notice = root.winfo_children()[0]
            message = notice.winfo_children()[0].winfo_children()[0].cget("text")
            assert "2026-06-18" in message, "Switching feedback is missing"
            assert args.switch_dataset.__self__ is app
            root.bind("<Destroy>", lambda event: closed.set() if event.widget == root else None)
            root.tk.call(root.protocol("WM_DELETE_WINDOW"))
            assert app.closing and root.winfo_exists()
            released.set()
            pump(root, closed.is_set)
            assert not alive(), "Reader survived switch cancellation"
    finally:
        released.set()
        if not closed.is_set():
            app.close()


def check_reopen_and_import_close(errors):
    from support import survey_downloads
    from aerial_atlas.datasets import find_dataset, prepare_downloads
    from aerial_atlas.mosaic import build
    with tempfile.TemporaryDirectory(prefix="atlas external ") as directory:
        work = Path(directory)
        project = work / "app"
        survey_downloads(work / "downloads")
        downloads = work / "downloads/images"
        prepare_downloads(downloads)
        survey = find_dataset(downloads, "1150618")
        build(survey.tiles, output_dir(project, survey.id), index_base=0, image=survey.image)
        args = parser().parse_args(["--project-dir", str(project), "--dataset", survey.id,
                                    "--settings-path", str(work / "settings.json")])
        root = tk.Tk()
        root.report_callback_exception = lambda *error: errors.append(str(error))
        app = Startup(root, args, source_dir(project, survey.id), output_dir(project, survey.id), work)
        try:
            pump(root, lambda: app.opened and app.viewer.detail_updates > 0)
            assert len(app.viewer.index.tiles) == 2
            assert not find_dataset(project / "images", survey.id).image
        finally:
            if app.opened:
                app.viewer.close()
            else:
                app.close()

        started, release, cleaned = threading.Event(), threading.Event(), threading.Event()

        def importing(folder, progress):
            started.set()
            try:
                assert release.wait(5), "Import test was not released"
                progress("importing", 0, 1, "download.zip")
            finally:
                cleaned.set()

        root = tk.Tk()
        root.report_callback_exception = lambda *error: errors.append(str(error))
        with patch("aerial_atlas.startup.prepare_downloads", side_effect=importing):
            app = Startup(root, args, source_dir(project, survey.id), output_dir(project, survey.id), work)
            try:
                pump(root, started.is_set)
                app.close()
                root.after_cancel(app.poll_job)
                app.poll_job = None
                app.poll()
                assert root.winfo_exists() and app.scan_running and not cleaned.is_set()
                release.set()
                pump(root, lambda: not app.scan_running)
                assert cleaned.is_set(), "Window closed before importer cleanup"
            finally:
                release.set()


def main():
    arguments=argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--screenshots",type=Path,help="Optional output directory, e.g. validation/screenshots")
    options=arguments.parse_args()
    if options.screenshots:
        options.screenshots.mkdir(parents=True,exist_ok=True)
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError,OSError):
        pass
    errors=[]
    ROOT.joinpath("validation").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas gui demo ") as directory:
        project=Path(directory)
        args=parser().parse_args(["--project-dir",str(project),"--settings-path",str(project/"settings.json"),"--tile","2_2"])
        root=tk.Tk()
        root.report_callback_exception=lambda *error: errors.append(str(error))
        app=Startup(root,args,source_dir(project),output_dir(project),project/"logs")
        try:
            pump(root,lambda: not app.scan_running)
            assert app.primary.instate(["disabled"])
            assert app.message_key=="missing"
            assert all(not button.instate(["disabled"]) for button in app.survey_picker.buttons.values())
            app.survey_picker.buttons["1150618"].invoke()
            pump(root, lambda: not app.scan_running)
            assert not app.data_button.instate(["disabled"]), "Missing survey blocked folder selection"
            app.survey_picker.buttons["1150427"].invoke()
            pump(root, lambda: not app.scan_running)
            args.tile = "2_2"
            # Both themes, translated strings and smaller preparation-window layout.
            app.theme.set("dark"); app.update_appearance()
            app.language.set("zh-TW"); app.update_appearance()
            root.geometry("780x660"); root.update()
            assert app.title.cget("text")=="從圖幅，看見全局。"
            pump(root,lambda: app.outer.winfo_reqheight()<=app.scroll.winfo_height()+2 or app.scrollbar.winfo_ismapped(),5)
            if app.outer.winfo_reqheight()>app.scroll.winfo_height()+2:
                assert app.scrollbar.winfo_ismapped()
                app.scroll.yview_moveto(1)
                root.update()
                assert app.primary.winfo_rooty()+app.primary.winfo_height()<=app.scroll.winfo_rooty()+app.scroll.winfo_height()
            app.theme.set("light"); app.language.set("en"); app.update_appearance()
            root.geometry("1040x720")
            app.scroll.yview_moveto(0)
            create_demo(source_dir(project))
            app.scan()
            pump(root,lambda: not app.scan_running)
            assert not app.primary.instate(["disabled"])
            # Synthetic-only public preparation screenshot.
            original_source,original_output=app.source_path.cget("text"),app.output_path.cget("text")
            app.source_path.configure(text="aerial-atlas / images / 1150427 / tiles_png_1024")
            app.output_path.configure(text="aerial-atlas / generated / 1150427")
            if options.screenshots:
                capture(root,options.screenshots/"preparation.png")
            app.source_path.configure(text=original_source); app.output_path.configure(text=original_output)
            # A native-call stand-in must be terminated without blocking Tk.
            app.progress_file=project/"hung-progress.json"
            app.cancel_file=project/"hung-cancel"
            worker_pid=project/"hung-worker.pid"
            stand_in="import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
            app.process=subprocess.Popen([sys.executable,"-c",stand_in,str(worker_pid)],
                                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            child=app.process
            worker_handle=None
            try:
                pump(root,lambda: worker_pid.is_file() and worker_pid.stat().st_size>0,5)
                actual_pid=int(worker_pid.read_text())
                if os.name=="nt":
                    from ctypes import wintypes
                    kernel=ctypes.windll.kernel32
                    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                    kernel.OpenProcess.restype=wintypes.HANDLE
                    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
                    kernel.WaitForSingleObject.restype=wintypes.DWORD
                    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
                    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                    worker_handle=kernel.OpenProcess(0x100001,False,actual_pid)
                    assert worker_handle,"Could not retain the real worker process handle"
                app.cancel()
                pump(root,lambda: app.process is None,8)
                assert child.poll() is not None
                if worker_handle:
                    pump(root,lambda: kernel.WaitForSingleObject(worker_handle,0)==0,3)
                assert app.killer is None,"Tree killer process was not reaped"
            finally:
                # Retained handles address our original worker even if a PID is
                # later reused; a failed smoke must not leave a sleep process.
                if worker_handle:
                    if kernel.WaitForSingleObject(worker_handle,0)==258:
                        kernel.TerminateProcess(worker_handle,1)
                        assert kernel.WaitForSingleObject(worker_handle,5000)==0
                    kernel.CloseHandle(worker_handle)
                if child.poll() is None:
                    if os.name=="nt":
                        subprocess.run(
                            [str(Path(os.environ["SystemRoot"])/"System32/taskkill.exe"),
                             "/PID",str(child.pid),"/T","/F"],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW,timeout=5,
                        )
                    else:
                        child.terminate()
                    child.wait(timeout=5)
            # The real subprocess creates/validates a map and opens the viewer in the same window.
            app.start_build()
            pump(root,lambda: app.opened and app.viewer.selected is not None and app.viewer.detail_updates>0,60)
            viewer=app.viewer
            assert viewer.selected.identifier=="02_02"
            w,h=viewer.dimensions()
            assert abs(min(w/viewer.camera.scale/1024,h/viewer.camera.scale/1024)-1.5)<1e-6
            viewer.locate_text("4_4")
            root.update()
            assert viewer.selected.identifier=="04_04"
            viewer.locate_text("2_2")
            pump(root,lambda: viewer.detail_updates>1)
            viewer.preferences.theme="dark"
            viewer._apply_theme()
            viewer.show_settings()
            root.update()
            viewer.close_settings()
            if options.screenshots:
                capture(root,options.screenshots/"demo.png")
            saved_center=[viewer.camera.cx,viewer.camera.cy,viewer.camera.scale]
            viewer.close()
        finally:
            try:
                if root.winfo_exists():
                    if app.opened:
                        app.viewer.close()
                    else:
                        app.close()
                        root.update()
            except tk.TclError: pass
        # Reopening uses exactly the previous camera and selected tile.
        args.tile=None
        root=tk.Tk()
        root.report_callback_exception=lambda *error: errors.append(str(error))
        app=Startup(root,args,source_dir(project),output_dir(project),project/"logs")
        try:
            pump(root,lambda: app.opened and app.viewer.selected is not None and app.viewer.detail_updates>0)
            viewer=app.viewer
            assert [viewer.camera.cx,viewer.camera.cy,viewer.camera.scale]==saved_center
            viewer.close()
        finally:
            try:
                if root.winfo_exists():
                    if app.opened:
                        app.viewer.close()
                    else:
                        app.close()
                        root.update()
            except tk.TclError: pass
        assert not errors,errors
        check_surveys(project, errors)
        check_close_during_survey_switch(project, errors)
        check_reopen_and_import_close(errors)
        assert not errors, errors
    atomic_json(ROOT/"validation/gui.json",dict(status="passed",missing_data=True,preparation_themes=True,
        high_dpi_layout=True,native_call_cancelled=True,gui_build=True,lookup_context=1.5,settings=True,
        exact_view_restored=True,survey_switching=True,download_import=True,
        survey_picker_keyboard=True,survey_picker_themes=True,switch_close=True,
        external_map_reopened=True,import_close_waits=True,callback_errors=errors))
    print("GUI checks passed")


if __name__=="__main__": main()
