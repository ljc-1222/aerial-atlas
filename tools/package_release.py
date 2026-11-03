"""Package the clean Windows build; GitHub supplies source archives from the tag."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from check_repo import ROOT, audit
from aerial_atlas import __version__
from aerial_atlas.mosaic import sha256


def archive(path,root,files):
    with ZipFile(path,"w",compression=ZIP_DEFLATED,compresslevel=6,allowZip64=True) as stream:
        stream.writestr("aerial-atlas/images/",b"")
        for file in files:
            stream.write(file,"aerial-atlas/"+file.relative_to(root).as_posix())
    with ZipFile(path) as stream:
        if stream.testzip() is not None: raise ValueError("Archive CRC check failed")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable",type=Path,default=ROOT/"dist/windows/aerial-atlas")
    args=parser.parse_args()
    destination=ROOT/"dist/releases"
    destination.mkdir(parents=True,exist_ok=True)
    portable_zip=destination/"aerial-atlas-windows-x64.zip"
    if portable_zip.exists():
        raise FileExistsError("Release archives already exist; preserve them or choose a new version")
    audit()
    portable=args.portable.resolve()
    manifest=json.loads((portable/"package_manifest.json").read_text(encoding="utf-8"))
    if manifest["version"]!=__version__:
        raise ValueError("Build version differs from the source version; rebuild before packaging")
    expected={item["path"] for item in manifest["files"]}|{"package_manifest.json"}
    actual={p.relative_to(portable).as_posix() for p in portable.rglob("*") if p.is_file()}
    if actual!=expected: raise ValueError("Portable folder has extra/missing files. Never package a used application folder.")
    for item in manifest["files"]:
        path=portable/item["path"]
        if path.stat().st_size!=item["bytes"] or sha256(path)!=item["sha256"]:
            raise ValueError(f"Portable file differs: {item['path']}")
    archive(portable_zip,portable,sorted(portable/path for path in expected))
    (destination/"SHA256SUMS.txt").write_text(f"{sha256(portable_zip)}  {portable_zip.name}\n",encoding="utf-8")
    print(f"Verified release archives written to {destination}")


if __name__=="__main__": main()
