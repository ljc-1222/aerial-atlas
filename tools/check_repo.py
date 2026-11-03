"""Check public source files and reject private or unapproved Git-tracked paths."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
TOP={"README.md","LICENSE","THIRD_PARTY_NOTICES.md","CONTRIBUTING.md",
     "pyproject.toml",".gitignore",".gitattributes","setup.cmd","start.cmd"}
TREES={"src","tests","tools","docs",".github","requirements"}
ASSETS={"docs/assets/demo.png"}
IGNORED_PARTS={"__pycache__",".pytest_cache"}


def public_files(root=ROOT):
    files=[]
    for name in sorted(TOP):
        if (root/name).is_file(): files.append(root/name)
    for tree in sorted(TREES):
        for path in sorted((root/tree).rglob("*")):
            if not path.is_file(): continue
            relative=path.relative_to(root)
            if any(part in IGNORED_PARTS or part.endswith(".egg-info") for part in relative.parts): continue
            if path.is_symlink(): raise ValueError(f"Symlink cannot be a release input: {relative}")
            if path.suffix.lower() in (".png",".ico"):
                if relative.as_posix() not in ASSETS: raise ValueError(f"Unapproved image: {relative}")
            elif path.suffix.lower() not in (".py",".md",".txt",".json",".yml",".yaml",".toml") and not path.name.lower().startswith(("license","copying","copyright","authors","notice")):
                raise ValueError(f"Unapproved release file: {relative}")
            files.append(path)
    files.append(root/"images/.gitkeep")
    return sorted(set(files))


def audit(root=ROOT):
    files=public_files(root)
    if (root/".git").exists():
        tracked=subprocess.check_output(["git","ls-files","-z"],cwd=root).decode("utf-8").split("\0")
        allowed={path.relative_to(root).as_posix() for path in files}
        unexpected=set(filter(None,tracked))-allowed
        if unexpected:
            raise ValueError("Unapproved or missing tracked files: "+", ".join(sorted(unexpected)))
    for path in files:
        relative=path.relative_to(root).as_posix()
        if not path.is_file(): raise ValueError(f"Missing public file: {relative}")
        if path.stat().st_size>2*1024**2: raise ValueError(f"Unexpectedly large public file: {relative}")
        if path.suffix.lower()==".png": continue
        text=path.read_text(encoding="utf-8")
        if re.search(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<|YOUR|example)[^\s\"']+",text,re.I):
            raise ValueError(f"Local user path found: {relative}")
        if re.search(r"(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_]{20,}",text):
            raise ValueError(f"Possible credential found: {relative}")
        if path.suffix==".md":
            for target in re.findall(r"\]\(([^)]+)\)",text):
                if re.match(r"(?:https?://|mailto:|#)",target): continue
                target=target.split("#")[0]
                if not (path.parent/target).exists():
                    raise ValueError(f"Broken local link in {relative}: {target}")
    return files


if __name__=="__main__":
    print(f"Public-file audit passed: {len(audit())} files")
