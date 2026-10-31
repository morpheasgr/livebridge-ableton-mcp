#!/usr/bin/env python3
"""LiveBridge updater for macOS and Windows (standard library only, Python 3.9+).

Brings this LiveBridge folder up to the newest version of its branch on GitHub, then runs the
installer (``installers/install.py``) so Live's Remote Script, the Max devices, the MCP server
and the skill are all current. Double-click ``Update LiveBridge.bat`` (Windows) or
``Update LiveBridge.command`` (macOS) in the folder, or run::

    python installers/update.py                 # update + install
    python installers/update.py --check         # only say whether there is a newer version
    python installers/update.py --no-install    # update the files only
    python installers/update.py -- --network    # pass options on to install.py

How it updates:

- A git checkout (a ``.git`` folder): ``git pull --ff-only`` — never over local changes.
- A downloaded folder (the GitHub zip): it downloads the branch's newest commit, checks it is
  LiveBridge, then copies it over this folder. Files you added (API dumps, notes, the
  installer's own files such as the MCP server's build info) are kept: only files an earlier
  update put here and the new version no longer has are removed (``.livebridge-update.json``
  lists them). Unchanged files are not rewritten.
- Nothing is changed when the download or the check fails; a file Windows keeps locked (Claude
  Desktop still running the MCP server from this folder) is reported, and the installer is not
  run — quit Claude Desktop and run the update again.

Where updates come from: ``installers/update_source.json`` (``{"repo": "owner/name",
"branch": "..."}``), or ``--repo`` / ``--branch``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILE = ROOT / "installers" / "update_source.json"
STATE_FILE = ".livebridge-update.json"
#: folders an update never touches (the developer venv, git, caches)
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".livebridge_work"}
#: code folders whose modules are auto-discovered: a module an old version had and the new one
#: does not would still register its tools, so the first update cleans their stale ``.py`` files
#: even without a list of what the last update put there
DISCOVERED = ("mcp_server/livebridge_mcp", "remote_script/LiveBridge")
#: a downloaded tree must have these to be LiveBridge
REQUIRED = ("installers/install.py", "remote_script/LiveBridge/__init__.py",
            "mcp_server/livebridge_mcp/__init__.py")
USER_AGENT = "livebridge-updater"


class UpdateError(Exception):
    """The update cannot go on; nothing was changed unless the message says so."""


# ----------------------------------------------------------------------------- download

def _fetch(url: str, timeout: float = 60.0) -> bytes:
    """GET ``url``; falls back to ``curl`` when Python has no certificates (python.org builds
    on macOS until "Install Certificates.command" was run)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"{url}: HTTP {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if shutil.which("curl") and isinstance(reason, ssl.SSLError):
            done = subprocess.run(["curl", "-fsSL", "-A", USER_AGENT, url],
                                  capture_output=True, timeout=timeout)
            if done.returncode == 0:
                return done.stdout
        raise UpdateError(f"{url}: {reason}") from exc


def latest_commit(repo: str, branch: str) -> str:
    data = json.loads(_fetch(f"https://api.github.com/repos/{repo}/commits/{branch}"))
    sha = data.get("sha") if isinstance(data, dict) else None
    if not sha:
        raise UpdateError(f"GitHub did not name the newest commit of {repo} {branch}")
    return str(sha)


def download(repo: str, sha: str, into: Path) -> Path:
    """Unpack ``repo`` at ``sha`` into ``into``; the folder holding the tree."""
    archive = into / "livebridge.zip"
    archive.write_bytes(_fetch(f"https://codeload.github.com/{repo}/zip/{sha}", timeout=300.0))
    return unpack(archive, into / "tree")


def unpack(archive: Path, into: Path) -> Path:
    """Extract a GitHub zip (one top folder) safely; the top folder. Keeps the executable bit
    of scripts (``.command``, ``.sh``) on macOS."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                target = (into / info.filename).resolve()
                if into.resolve() not in target.parents and target != into.resolve():
                    raise UpdateError(f"unsafe path in the download: {info.filename}")
            bundle.extractall(into)
            if os.name != "nt":
                for info in bundle.infolist():
                    mode = (info.external_attr >> 16) & 0o777
                    if mode & 0o111:
                        path = into / info.filename
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"the download is not a zip archive ({exc})") from exc
    tops = [p for p in into.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise UpdateError("the download does not have the layout of a GitHub archive")
    missing = [r for r in REQUIRED if not (tops[0] / r).is_file()]
    if missing:
        raise UpdateError("the download is not LiveBridge (missing " + ", ".join(missing) + ")")
    return tops[0]


# ----------------------------------------------------------------------------- sync

def files_of(tree: Path) -> List[str]:
    """Every file of ``tree`` as a ``/`` path, minus SKIP_DIRS."""
    found = []
    for folder, dirs, files in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            found.append((Path(folder) / name).relative_to(tree).as_posix())
    return found


def _same(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()
    except OSError:
        return False


def read_state(root: Path) -> Dict[str, object]:
    try:
        data = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def sync(new: Path, root: Path, previous: Optional[Iterable[str]]) -> Dict[str, List[str]]:
    """Make ``root`` hold ``new``'s files: copy what is new or changed, remove what the last
    update put there (``previous``) and ``new`` no longer has. Without ``previous`` (the first
    update) only stale modules of the auto-discovered code folders are removed.

    Returns ``{added, changed, removed, locked}``; ``locked`` files could not be written or
    removed (Windows: in use)."""
    wanted = files_of(new)
    wanted_set = set(wanted)
    report: Dict[str, List[str]] = {"added": [], "changed": [], "removed": [], "locked": []}
    for rel in wanted:
        source, target = new / rel, root / rel
        if target.exists() and _same(source, target):
            continue                        # also keeps a running .bat/.command untouched
        existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError:
            report["locked"].append(rel)
            continue
        report["changed" if existed else "added"].append(rel)
    if previous is not None:
        stale = [rel for rel in previous if rel not in wanted_set]
    else:
        stale = [rel for rel in files_of(root) if rel.endswith(".py") and rel not in wanted_set
                 and any(rel.startswith(folder + "/") for folder in DISCOVERED)]
    for rel in sorted(stale):
        path = root / rel
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            report["locked"].append(rel)
            continue
        report["removed"].append(rel)
        parent = path.parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return report


# ----------------------------------------------------------------------------- main

def source(args: argparse.Namespace) -> Tuple[str, str]:
    data: Dict[str, str] = {}
    try:
        loaded = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
        data = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        pass
    repo = args.repo or data.get("repo") or "morpheasgr/livebridge-ableton-mcp"
    branch = args.branch or data.get("branch") or "main"
    return repo, branch


def git_update(root: Path) -> int:
    git = shutil.which("git")
    if not git:
        raise UpdateError("this folder is a git checkout but git is not on PATH: run "
                          "`git pull` yourself, then the installer")
    dirty = subprocess.run([git, "status", "--porcelain", "--untracked-files=no"], cwd=root,
                           capture_output=True, text=True)
    if dirty.stdout.strip():
        raise UpdateError("the checkout has local changes: commit or stash them, then update "
                          "(nothing was changed)")
    print("git checkout: git pull --ff-only")
    return subprocess.run([git, "pull", "--ff-only"], cwd=root).returncode


def run_installer(root: Path, extra: List[str]) -> int:
    print("\nRunning the installer ...\n")
    return subprocess.run([sys.executable, str(root / "installers" / "install.py"), *extra],
                          cwd=root).returncode


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: List[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, extra = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(description="Update LiveBridge to the newest version and "
                                                 "run the installer.")
    parser.add_argument("--check", action="store_true", help="only say whether there is a "
                        "newer version")
    parser.add_argument("--no-install", dest="install", action="store_false",
                        help="update the files, do not run the installer")
    parser.add_argument("--repo", help="GitHub owner/name (default: installers/"
                        "update_source.json)")
    parser.add_argument("--branch", help="branch (default: installers/update_source.json)")
    parser.add_argument("--force", action="store_true", help="download and copy even when "
                        "this version is already the newest")
    args = parser.parse_args(argv)
    root = ROOT
    try:
        if (root / ".git").exists() and not args.check:
            code = git_update(root)
            if code != 0:
                return code
            return run_installer(root, extra) if args.install else 0
        repo, branch = source(args)
        state = read_state(root)
        print(f"Looking for the newest LiveBridge ({repo}, branch {branch}) ...")
        sha = latest_commit(repo, branch)
        current = state.get("commit")
        if args.check:
            if current == sha:
                print(f"Up to date ({sha[:12]}).")
            else:
                print(f"A newer version is available: {sha[:12]} (this folder: "
                      f"{str(current)[:12] if current else 'unknown'}).")
            return 0
        if current == sha and not args.force:
            print(f"Already the newest version ({sha[:12]}).")
            return run_installer(root, extra) if args.install else 0
        with tempfile.TemporaryDirectory(prefix="livebridge-update-") as temp:
            print(f"Downloading {sha[:12]} ...")
            tree = download(repo, sha, Path(temp))
            previous = state.get("files") if isinstance(state.get("files"), list) else None
            report = sync(tree, root, previous)
            files = files_of(tree)
        print(f"{len(report['added'])} files added, {len(report['changed'])} changed, "
              f"{len(report['removed'])} removed.")
        if report["locked"]:
            # remember only what is really there, so the next run finishes the job
            (root / STATE_FILE).write_text(json.dumps(
                {"repo": repo, "branch": branch, "commit": None,
                 "files": sorted(set(files) | set(previous or []))}, indent=1),
                encoding="utf-8")
            print("\nThese files are in use and were not updated:")
            for rel in report["locked"][:20]:
                print("  " + rel)
            print("\nQuit Claude Desktop (also from the tray / menu bar icon) and Ableton Live, "
                  "then run the update again. The installer was not run.")
            return 2
        (root / STATE_FILE).write_text(json.dumps(
            {"repo": repo, "branch": branch, "commit": sha, "files": files}, indent=1),
            encoding="utf-8")
        print(f"LiveBridge is now at {sha[:12]}.")
        return run_installer(root, extra) if args.install else 0
    except UpdateError as exc:
        print(f"\nUpdate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
