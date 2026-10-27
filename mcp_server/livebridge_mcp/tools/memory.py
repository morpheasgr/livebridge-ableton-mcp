"""Session memory: what was decided, what is still open and what the user likes, kept across
sessions. `live_memory_note` stores an entry, `live_memory_recall` reads the project's entries
(and the user's general preferences) at the start of a session, `live_memory_update` marks an
idea done or rewrites it, `live_memory_forget` deletes one.

Live's API does not tell which file is open, so each project is identified by its name plus a
fingerprint of the set (tempo and track names) taken when a note is written; recall without a
project name matches the open set against those fingerprints.

Stored in ``~/.livebridge/memory.json`` on the machine of the MCP server
(:mod:`livebridge_mcp.library`).
"""

from __future__ import annotations

import time
from typing import Any

from . import bridge_call, tool_error
from .. import library
from ..client import BridgeClient

KINDS = ("decision", "idea", "todo", "preference", "note")
_DEFAULT_PROJECT = "default"
_MATCH_THRESHOLD = 0.5
_MAX_TEXT = 1000


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _path():
    return library.library_dir() / "memory.json"


def _load() -> dict[str, Any]:
    data = library.read_json(_path(), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("current", None)
    data.setdefault("projects", {})
    data.setdefault("entries", [])
    return data


def _similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """0..1: how alike two set fingerprints are (track names, tempo as a tie-break)."""
    names_a = {n.lower() for n in a.get("tracks") or []}
    names_b = {n.lower() for n in b.get("tracks") or []}
    if not names_a or not names_b:
        return 0.0
    score = len(names_a & names_b) / len(names_a | names_b)
    if a.get("tempo") and b.get("tempo") and abs(a["tempo"] - b["tempo"]) < 0.5:
        score = min(1.0, score + 0.1)
    return round(score, 2)


def _entry_view(entry: dict[str, Any]) -> dict[str, Any]:
    view = {k: entry[k] for k in ("id", "kind", "text", "created") if k in entry}
    if entry.get("tags"):
        view["tags"] = entry["tags"]
    if entry.get("done"):
        view["done"] = True
    if entry.get("project") is not None:
        view["project"] = entry["project"]
    return view


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the session memory tools on the MCP app."""

    def fingerprint() -> dict[str, Any] | None:
        """Tempo and track names of the open set, or None when Live is not reachable."""
        tracks = bridge_call(bridge, "tracks.list", {"detail": "minimal",
                                                     "include_returns": False,
                                                     "include_master": False})
        if _is_error(tracks) or not isinstance(tracks, dict):
            return None
        names = [str(t.get("name")) for t in tracks.get("tracks") or []
                 if isinstance(t, dict) and t.get("name")]
        state = bridge_call(bridge, "transport.get")
        tempo = state.get("tempo") if isinstance(state, dict) and not _is_error(state) else None
        return {"tempo": tempo, "tracks": names[:64]}

    def save(data: dict[str, Any], cmd: str) -> dict[str, Any] | None:
        try:
            library.write_json(_path(), data)
        except OSError as exc:
            return tool_error(f"could not write {_path()}: {exc}", type="internal", cmd=cmd)
        return None

    @mcp.tool()
    def live_memory_note(
        text: str,
        kind: str = "note",
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> Any:
        """Remember something about this project (or the user) for later sessions: a decision
        the user kept ("kept the Reese with 25-cent detune; filter sweep on the bridge"), an
        open idea ("the chorus needs more lift"), a todo, or a general preference ("likes dark,
        dry mixes; hates big reverbs on drums"). Write short, specific notes right after the
        user approves or rejects something.

        Args:
            text: The note (one or two sentences).
            kind: "decision", "idea" (open creative thread), "todo", "preference" (about the
                user, applies to every project) or "note".
            project: Project name ("Night Drive"). Omitted = the current project (the last one
                used); the first note of a new project needs a name.
            tags: Optional words to find it again (["bass", "drop"]).

        Returns:
            `{id, project, kind, text, created, fingerprint?}`.
        """
        cmd = "memory.note"
        if not isinstance(text, str) or not text.strip():
            return tool_error("text must be a non-empty string", cmd=cmd)
        if len(text) > _MAX_TEXT:
            return tool_error(f"keep notes under {_MAX_TEXT} characters", cmd=cmd)
        if kind not in KINDS:
            return tool_error(f"kind must be one of {', '.join(KINDS)}", cmd=cmd)
        if project is not None and (not isinstance(project, str) or not project.strip()):
            return tool_error("project must be a non-empty name", cmd=cmd)
        data = _load()
        entry: dict[str, Any] = {
            "id": max((e.get("id", 0) for e in data["entries"]), default=0) + 1,
            "kind": kind, "text": text.strip(),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if tags:
            entry["tags"] = sorted({str(t).strip().lower() for t in tags if str(t).strip()})
        result_project = None
        stamped = None
        if kind != "preference":
            result_project = (project or data["current"] or _DEFAULT_PROJECT).strip()
            entry["project"] = result_project
            info = data["projects"].setdefault(result_project, {})
            current = fingerprint()
            if current and current["tracks"]:
                info["fingerprint"] = current
                stamped = current
            info["updated"] = entry["created"]
            data["current"] = result_project
        else:
            entry["project"] = None
        data["entries"].append(entry)
        error = save(data, cmd)
        if error:
            return error
        result = dict(_entry_view(entry), project=result_project)
        if stamped:
            result["fingerprint"] = {"tempo": stamped["tempo"],
                                     "tracks": len(stamped["tracks"])}
        return result

    @mcp.tool()
    def live_memory_recall(
        project: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        include_done: bool = False,
        limit: int = 40,
    ) -> Any:
        """Recall what was decided and what is still open in this project, plus the user's
        general preferences — call it at the start of a session ("last time you kept the filter
        sweep for the bridge; the chorus-lift idea is still open") and before big decisions.
        Read-only.

        Args:
            project: Project name. Omitted = the open set is matched against the stored
                projects by its track names and tempo; without a match, the last project used.
            kind: Only "decision", "idea", "todo", "preference" or "note".
            query: Only entries containing this text.
            include_done: Also ideas/todos marked done.
            limit: Newest N entries of the project (1..200).

        Returns:
            `{project, matched_by ("name"|"fingerprint"|"last_used"|"none"), similarity?,
            entries:[{id, kind, text, created, tags?, done?}], preferences:[...], projects:
            [{name, updated, entries}]}`.
        """
        cmd = "memory.recall"
        if kind is not None and kind not in KINDS:
            return tool_error(f"kind must be one of {', '.join(KINDS)}", cmd=cmd)
        if not 1 <= limit <= 200:
            return tool_error("limit must be 1..200", cmd=cmd)
        data = _load()
        matched_by, similarity, chosen = "none", None, None
        if project:
            chosen = next((name for name in data["projects"]
                           if name.lower() == project.strip().lower()), project.strip())
            matched_by = "name"
        else:
            current = fingerprint()
            if current:
                scored = sorted(((_similarity(current, info.get("fingerprint") or {}), name)
                                 for name, info in data["projects"].items()), reverse=True)
                if scored and scored[0][0] >= _MATCH_THRESHOLD:
                    similarity, chosen = scored[0]
                    matched_by = "fingerprint"
            if chosen is None and data["current"]:
                chosen, matched_by = data["current"], "last_used"

        def wanted(entry: dict[str, Any]) -> bool:
            if kind and entry.get("kind") != kind:
                return False
            if query and query.lower() not in entry.get("text", "").lower():
                return False
            return include_done or not entry.get("done")

        entries = [_entry_view(e) for e in data["entries"]
                   if chosen is not None and e.get("project") == chosen and wanted(e)]
        preferences = [_entry_view(e) for e in data["entries"]
                       if e.get("project") is None and (kind in (None, "preference"))
                       and wanted(e)]
        projects = [{"name": name, "updated": info.get("updated"),
                     "entries": sum(1 for e in data["entries"] if e.get("project") == name)}
                    for name, info in data["projects"].items()]
        projects.sort(key=lambda p: p["updated"] or "", reverse=True)
        result: dict[str, Any] = {"project": chosen, "matched_by": matched_by,
                                  "entries": entries[-limit:], "preferences": preferences,
                                  "projects": projects[:20]}
        if similarity is not None:
            result["similarity"] = similarity
        if chosen is not None and matched_by != "name" and data["current"] != chosen:
            data["current"] = chosen
            save(data, cmd)
        return result

    @mcp.tool()
    def live_memory_update(
        id: int,
        done: bool | None = None,
        text: str | None = None,
        kind: str | None = None,
    ) -> Any:
        """Change a remembered entry: mark an idea or todo done (it stops showing in recall),
        reopen it, rewrite its text or change its kind (an idea that became a decision).

        Args:
            id: The entry id from `live_memory_recall` / `live_memory_note`.
            done: true = done, false = open again.
            text: New text.
            kind: New kind ("decision", "idea", "todo", "preference", "note").

        Returns:
            The updated entry.
        """
        cmd = "memory.update"
        if done is None and text is None and kind is None:
            return tool_error("pass done, text or kind", cmd=cmd)
        if kind is not None and kind not in KINDS:
            return tool_error(f"kind must be one of {', '.join(KINDS)}", cmd=cmd)
        if text is not None and (not text.strip() or len(text) > _MAX_TEXT):
            return tool_error(f"text must be 1..{_MAX_TEXT} characters", cmd=cmd)
        data = _load()
        entry = next((e for e in data["entries"] if e.get("id") == id), None)
        if entry is None:
            return tool_error(f"no memory entry {id}", type="not_found", cmd=cmd)
        if done is not None:
            entry["done"] = bool(done)
        if text is not None:
            entry["text"] = text.strip()
        if kind is not None:
            if kind == "preference":
                entry["project"] = None
            elif entry.get("project") is None:
                entry["project"] = data["current"] or _DEFAULT_PROJECT
            entry["kind"] = kind
        entry["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        error = save(data, cmd)
        return error or _entry_view(entry)

    @mcp.tool()
    def live_memory_forget(id: int | None = None, project: str | None = None) -> Any:
        """Delete remembered entries: one entry by id, or a whole project's memory. Ask the user
        before forgetting a project — this cannot be undone.

        Args:
            id: One entry.
            project: Or every entry of this project (and the project itself).

        Returns:
            `{deleted: n, project?}`.
        """
        cmd = "memory.forget"
        if (id is None) == (project is None):
            return tool_error("pass either id or project", cmd=cmd)
        data = _load()
        before = len(data["entries"])
        if id is not None:
            data["entries"] = [e for e in data["entries"] if e.get("id") != id]
            if len(data["entries"]) == before:
                return tool_error(f"no memory entry {id}", type="not_found", cmd=cmd)
        else:
            name = next((n for n in data["projects"] if n.lower() == project.strip().lower()),
                        None)
            if name is None:
                return tool_error(f"no project named {project!r} in memory", type="not_found",
                                  cmd=cmd)
            data["entries"] = [e for e in data["entries"] if e.get("project") != name]
            del data["projects"][name]
            if data["current"] == name:
                data["current"] = None
        error = save(data, cmd)
        if error:
            return error
        result: dict[str, Any] = {"deleted": before - len(data["entries"])}
        if project is not None:
            result["project"] = project
        return result
