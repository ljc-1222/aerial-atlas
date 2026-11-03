"""Interactive Windows smoke/regression checks using synthetic inputs only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk

from PIL import ImageGrab
from aerial_atlas.app import parser
from aerial_atlas.demo import create_demo
from aerial_atlas.paths import output_dir, source_dir, atomic_json
from aerial_atlas.startup import Startup

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
            app.source_path.configure(text="aerial-atlas / images / 1150427 / RGB_png")
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
                if root.winfo_exists(): app.close(); root.update()
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
                if root.winfo_exists(): app.close(); root.update()
            except tk.TclError: pass
        assert not errors,errors
    atomic_json(ROOT/"validation/gui.json",dict(status="passed",missing_data=True,preparation_themes=True,
        high_dpi_layout=True,native_call_cancelled=True,gui_build=True,lookup_context=1.5,settings=True,exact_view_restored=True,callback_errors=errors))
    print("GUI checks passed")


if __name__=="__main__": main()
