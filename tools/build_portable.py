"""Build a data-free Windows onedir application from the installed project."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from licenses import collect
from aerial_atlas import __version__
from aerial_atlas.mosaic import sha256


def main():
    if os.name!="nt": raise SystemExit("Portable releases must be built on Windows x64")
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=root/"dist/windows/aerial-atlas")
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists(): raise FileExistsError("Choose a new output folder; existing releases are preserved")
    work=root/".build/pyinstaller"
    work.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,"-m","PyInstaller","--noconfirm","--onedir","--windowed","--noupx",
             "--name","AerialAtlas","--distpath",str(work/"dist"),"--workpath",str(work/"work"),
             "--specpath",str(work),"--paths",str(root/"src"),"--collect-all","rasterio",
             "--copy-metadata","rasterio","--copy-metadata","numpy","--copy-metadata","Pillow",
             str(root/"tools/launch_frozen.py")]
    with (work/"build.log").open("w",encoding="utf-8") as log:
        subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT,check=True)
    output.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(work/"dist/AerialAtlas",output)
    for name in ("LICENSE","THIRD_PARTY_NOTICES.md"):
        shutil.copy2(root/name,output/name)
    shutil.copy2(root/"tools/resources/portable-readme.txt",output/"README.txt")
    (output/"images").mkdir()
    collect(output/"licenses", output)
    inventory=[dict(path=p.relative_to(output).as_posix(),bytes=p.stat().st_size,sha256=sha256(p))
               for p in sorted(output.rglob("*")) if p.is_file()]
    (output/"package_manifest.json").write_text(json.dumps(dict(version=__version__,files=inventory),indent=2),encoding="utf-8")
    print(f"Built data-free portable application: {output}")


if __name__=="__main__": main()
