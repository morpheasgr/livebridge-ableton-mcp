"""installers/update.py: download, check and sync a LiveBridge folder (no network)."""

import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lb_update", ROOT / "installers" / "update.py")
update = importlib.util.module_from_spec(spec)
sys.modules["lb_update"] = update
spec.loader.exec_module(update)


def write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


BASE = {"installers/install.py": "print('install')\n",
        "remote_script/LiveBridge/__init__.py": "",
        "mcp_server/livebridge_mcp/__init__.py": "",
        "README.md": "v1\n"}


def make_zip(tmp_path, files, name="repo-abc"):
    archive = tmp_path / "dl.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for rel, text in files.items():
            info = zipfile.ZipInfo(f"{name}/{rel}")
            info.external_attr = (0o755 if rel.endswith(".command") else 0o644) << 16
            bundle.writestr(info, text)
    return archive


def test_sync_adds_changes_removes_and_keeps_user_files(tmp_path):
    root, new = tmp_path / "root", tmp_path / "new"
    write(root, dict(BASE, **{"old.txt": "gone in v2", "docs/my_notes.md": "mine"}))
    write(new, dict(BASE, **{"README.md": "v2\n", "added.txt": "new"}))
    unchanged = root / "installers/install.py"
    stamp = unchanged.stat().st_mtime_ns
    os.utime(unchanged, ns=(stamp - 10**9, stamp - 10**9))
    report = update.sync(new, root, previous=list(BASE) + ["old.txt"])
    assert report["added"] == ["added.txt"] and report["changed"] == ["README.md"]
    assert report["removed"] == ["old.txt"] and report["locked"] == []
    assert (root / "README.md").read_text() == "v2\n"
    assert (root / "docs/my_notes.md").exists()                 # never listed: the user's
    assert unchanged.stat().st_mtime_ns == stamp - 10**9          # same bytes: not rewritten


def test_first_update_removes_only_stale_discovered_modules(tmp_path):
    root, new = tmp_path / "root", tmp_path / "new"
    write(root, dict(BASE, **{"mcp_server/livebridge_mcp/tools/old_tool.py": "x",
                              "remote_script/LiveBridge/config.json": "{}",
                              "docs/LIVE_API_DUMP_mine.md": "dump"}))
    write(new, BASE)
    report = update.sync(new, root, previous=None)
    assert report["removed"] == ["mcp_server/livebridge_mcp/tools/old_tool.py"]
    assert not (root / "mcp_server/livebridge_mcp/tools").exists()   # emptied folder gone
    assert (root / "remote_script/LiveBridge/config.json").exists()
    assert (root / "docs/LIVE_API_DUMP_mine.md").exists()


def test_unpack_checks_the_archive_and_keeps_the_executable_bit(tmp_path):
    good = make_zip(tmp_path, dict(BASE, **{"Update LiveBridge.command": "#!/bin/bash\n"}))
    tree = update.unpack(good, tmp_path / "out")
    assert (tree / "installers/install.py").is_file()
    if os.name != "nt":
        assert os.access(tree / "Update LiveBridge.command", os.X_OK)
    bad = make_zip(tmp_path, {"README.md": "not livebridge"})
    with pytest.raises(update.UpdateError, match="not LiveBridge"):
        update.unpack(bad, tmp_path / "out2")


def test_main_updates_records_the_commit_and_runs_the_installer(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write(root, BASE)
    archive = make_zip(tmp_path, dict(BASE, **{"README.md": "v2\n"}))
    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "latest_commit", lambda repo, branch: "f" * 40)
    monkeypatch.setattr(update, "_fetch", lambda url, timeout=60.0: archive.read_bytes())
    ran = []
    monkeypatch.setattr(update, "run_installer", lambda r, extra: ran.append(extra) or 0)
    assert update.main(["--repo", "o/r", "--branch", "b", "--", "--network"]) == 0
    state = json.loads((root / update.STATE_FILE).read_text())
    assert state["commit"] == "f" * 40 and "README.md" in state["files"]
    assert (root / "README.md").read_text() == "v2\n" and ran == [["--network"]]
    # the same commit again: nothing downloaded, the installer still runs
    monkeypatch.setattr(update, "_fetch", lambda *a, **k: pytest.fail("downloaded again"))
    assert update.main(["--repo", "o/r", "--branch", "b"]) == 0 and len(ran) == 2


def test_a_failed_download_changes_nothing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    write(root, BASE)
    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "latest_commit", lambda repo, branch: "a" * 40)

    def offline(url, timeout=60.0):
        raise update.UpdateError("network down")
    monkeypatch.setattr(update, "_fetch", offline)
    assert update.main(["--no-install"]) == 1
    assert "network down" in capsys.readouterr().err
    assert (root / "README.md").read_text() == "v1\n" and not (root / update.STATE_FILE).exists()


def test_locked_files_stop_before_the_installer(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write(root, BASE)
    archive = make_zip(tmp_path, dict(BASE, **{"README.md": "v2\n"}))
    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "latest_commit", lambda repo, branch: "b" * 40)
    monkeypatch.setattr(update, "_fetch", lambda url, timeout=60.0: archive.read_bytes())
    real_copy = update.shutil.copy2

    def locked(source, target):
        if str(target).endswith("README.md"):
            raise PermissionError("in use")
        return real_copy(source, target)
    monkeypatch.setattr(update.shutil, "copy2", locked)
    monkeypatch.setattr(update, "run_installer", lambda *a: pytest.fail("installer ran"))
    assert update.main([]) == 2
    assert json.loads((root / update.STATE_FILE).read_text())["commit"] is None


def test_a_git_checkout_pulls_instead(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write(root, BASE)
    (root / ".git").mkdir()
    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "git_update", lambda r: 0)
    monkeypatch.setattr(update, "latest_commit", lambda *a: pytest.fail("downloaded"))
    assert update.main(["--no-install"]) == 0


def test_the_launchers_and_the_source():
    data = json.loads((ROOT / "installers/update_source.json").read_text())
    assert "/" in data["repo"] and data["branch"]
    for name in ("Install LiveBridge.bat", "Update LiveBridge.bat"):
        text = (ROOT / name).read_text()
        assert "installers\\install.ps1" in text
    assert "--update" in (ROOT / "Update LiveBridge.bat").read_text()
    for name in ("Install LiveBridge.command", "Update LiveBridge.command"):
        text = (ROOT / name).read_text()
        assert text.startswith("#!/bin/bash") and "installers/install.sh" in text
        if os.name != "nt":
            assert os.access(ROOT / name, os.X_OK)
