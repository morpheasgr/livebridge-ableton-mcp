"""Sample search over everything Live's browser shows under Library > Samples, with Live's
browser tags: `live_sample_search` finds samples by words and tags ("a punchy kick" ->
tags=["Drums|Kick", "Character|Punchy"], kind="one_shot") and returns absolute file paths for
`live_drumrack_set_pad(file_path=...)` / `live_sample_import`, and finds samples that sound like
another one (Live's own audio features); `live_sample_duplicates` lists identical audio shipped
more than once; `live_sample_tags` lists the tag vocabulary; `live_sample_index` shows or
refreshes the index.

The engine (:mod:`livebridge_mcp.sample_db`) reads a temporary copy of Live's browser database
in this process — never Live's file itself and never inside Live — and keeps a local index in
``~/.livebridge/sample_index.sqlite``.

Settings (``~/.livebridge/config.json``, all optional)::

    "sample_index": {
        "roots": {"Samples": "D:\\\\Samples"},   # place name -> folder, when not found
        "database": "...",                       # Live-files-<N>.db or its folder
        "auto_refresh_seconds": 300              # 0 = refresh only on request
    }

``LIVEBRIDGE_LIVE_DATABASE`` overrides ``database``.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from . import bridge_call, tool_error
from .. import library, sample_db
from ..client import BridgeClient
from ..config import read_config_file

_KINDS = ("one_shot", "loop")
_ORDERS = ("relevance", "name", "newest", "most_used", "random")
_ACTIONS = ("status", "refresh")
_DEFAULT_AUTO_REFRESH = 300.0
_INDEX_NAME = "sample_index.sqlite"
_MAX_SEARCHES = 16
#: The keys of one search (live_sample_search arguments) and their defaults.
_SEARCH_DEFAULTS: dict[str, Any] = {
    "query": None, "tags": None, "any_tags": None, "exclude_tags": None, "kind": None,
    "place": None, "source": None, "category": None, "extensions": None, "similar_to": None,
    "include_copies": False, "order": "relevance", "seed": None, "offset": 0, "limit": 20}
_PLUGIN_KINDS = ("instrument", "audio_effect", "midi_effect")


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _settings() -> dict[str, Any]:
    section = read_config_file().get("sample_index")
    return section if isinstance(section, dict) else {}


def _index_path():
    return library.library_dir() / _INDEX_NAME


def _list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the sample search tools on the MCP app."""

    def ensure(refresh: bool, cmd: str) -> dict[str, Any]:
        """Build or refresh the index when needed. Returns ``{"index": {...}}`` info or an
        error dict."""
        settings = _settings()
        overrides = settings.get("roots") if isinstance(settings.get("roots"), dict) else {}
        override_db = os.environ.get("LIVEBRIDGE_LIVE_DATABASE") or settings.get("database")
        database = sample_db.find_live_database(override_db)
        if database is None:
            where = override_db or " or ".join(str(d) for d in sample_db.database_dirs())
            return tool_error(
                f"Live's browser database (Live-files-<N>.db) was not found in {where}. It "
                "exists on the computer that runs Live once Live has indexed its browser; in "
                "LAN mode the MCP server's computer has no copy (set sample_index.database in "
                "~/.livebridge/config.json to a copied file).", type="not_found", cmd=cmd)
        index_path = _index_path()
        meta = sample_db.read_meta(index_path)
        signature = sample_db.source_signature(database)
        stale = bool(meta) and meta.get("signature") != signature
        rebuild = refresh or not meta or meta.get("schema") != sample_db.SCHEMA_VERSION \
            or meta.get("source_db") != str(database) or meta.get("overrides") != overrides
        if stale and not rebuild:
            try:
                auto = float(settings.get("auto_refresh_seconds", _DEFAULT_AUTO_REFRESH))
            except (TypeError, ValueError):
                auto = _DEFAULT_AUTO_REFRESH
            rebuild = auto > 0 and time.time() - float(meta.get("built_at", 0)) >= auto
        info: dict[str, Any] = {}
        if rebuild:
            hints = bridge_call(bridge, "samples.locations")
            if _is_error(hints) or not isinstance(hints, dict):
                hints = {}
                info["live_reachable"] = False
            try:
                status = sample_db.build_index(index_path, database, overrides, hints)
            except sample_db.SampleIndexError as exc:
                return tool_error(str(exc), type="invalid_state", cmd=cmd)
            except (OSError, sqlite3.DatabaseError) as exc:
                return tool_error(f"building the sample index failed: {exc}",
                                  type="invalid_state", cmd=cmd)
            info.update(refreshed=True, samples=status.get("samples"),
                        build_seconds=status.get("build_seconds"))
            if status.get("unresolved"):
                info["unresolved_places"] = status["unresolved"]
        elif stale:
            info["stale"] = True
        return {"index": info}

    def check_search(spec: dict[str, Any], cmd: str, label: str) -> dict[str, Any] | None:
        """A friendly error for a bad search spec, or None."""
        similar_to = spec["similar_to"]
        if similar_to is not None and not str(similar_to).strip():
            return tool_error(f"{label}similar_to must be a path or file name", cmd=cmd)
        if spec["kind"] is not None and spec["kind"] not in _KINDS:
            return tool_error(f"{label}kind must be one of {', '.join(_KINDS)}", cmd=cmd)
        if spec["order"] not in _ORDERS:
            return tool_error(f"{label}order must be one of {', '.join(_ORDERS)}", cmd=cmd)
        offset, limit = spec["offset"], spec["limit"]
        if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 \
                or not 1 <= limit <= 200:
            return tool_error(f"{label}offset must be >= 0 and limit 1..200", cmd=cmd)
        for key in ("tags", "any_tags", "exclude_tags"):
            value = spec[key]
            if value is not None and (not isinstance(value, list)
                                      or any(not str(v).strip() for v in value)):
                return tool_error(f"{label}{key} must be a list of non-empty tags", cmd=cmd)
        return None

    def run_search(spec: dict[str, Any], cmd: str) -> dict[str, Any]:
        """One search against the index: the result, or a friendly error dict."""
        extensions = spec["extensions"]
        filters = dict(
            query=spec["query"], tags=spec["tags"], any_tags=spec["any_tags"],
            exclude_tags=spec["exclude_tags"], kind=spec["kind"], places=_list(spec["place"]),
            sources=_list(spec["source"]), categories=_list(spec["category"]),
            extensions=[e.strip() for e in str(extensions).split(",") if e.strip()]
            if extensions else None, offset=spec["offset"], limit=spec["limit"])
        try:
            if spec["similar_to"] is not None:
                return sample_db.similar(_index_path(), str(spec["similar_to"]),
                                         include_copies=bool(spec["include_copies"]),
                                         **filters)
            return sample_db.search(_index_path(), order=spec["order"], seed=spec["seed"],
                                    **filters)
        except sample_db.SampleIndexError as exc:
            return tool_error(str(exc), type="not_found", cmd=cmd)
        except sqlite3.DatabaseError as exc:
            return tool_error(f"the sample index is unreadable ({exc}) — rebuild it with "
                              "live_sample_index(action='refresh')", type="invalid_state",
                              cmd=cmd)

    @mcp.tool()
    def live_sample_search(
        query: str | None = None,
        tags: list[str] | None = None,
        any_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        kind: str | None = None,
        place: str | list[str] | None = None,
        source: str | list[str] | None = None,
        category: str | list[str] | None = None,
        extensions: str | None = None,
        similar_to: str | None = None,
        include_copies: bool = False,
        order: str = "relevance",
        seed: int | None = None,
        offset: int = 0,
        limit: int = 20,
        searches: list[dict[str, Any]] | None = None,
        refresh: bool = False,
    ) -> Any:
        """Search every sample Live's browser knows (Library > Samples: Core Library, packs,
        User Library and all folders added to Places — tens of thousands of files, far more
        than the Live API lists) by words and Live's browser tags, with the absolute file
        path of each result. Load a result with `live_drumrack_set_pad(note=..., file_path=
        <path>)`, `live_sample_import(file_path=<path>, ...)` or `live_simpler_action(
        "replace_sample", file_path=<path>)`. Read-only.

        Args:
            query: Words that must all appear in the file name, its folders or its place
                ("909 kick", "vinyl crackle", "amen 170").
            tags: Tags that must ALL be present: "Group|Tag" ("Drums|Kick", "Character|
                Punchy", "Sounds|Bass"), just "Tag" (any group: "Punchy"), or "Group|*" (any
                tag of that group). Case-insensitive. `live_sample_tags` lists what exists.
            any_tags: At least one of these tags (same forms).
            exclude_tags: None of these tags.
            kind: "one_shot" or "loop" (from the Type tag, else guessed from the path).
            place: Only these places ("Core Library", "Samples", "User Library", a pack name).
            source: Only these kinds of place: "core_library", "pack", "user_library",
                "user_folder" (folders added to Places), "current_project", "other".
            category: Live's browser sound categories ("Bass", "Booms", "Ambient &
                Evolving", or "Group/Name" when a name exists in two groups); several = all
                must match. `live_sample_tags(categories=true)` lists them.
            extensions: Only these file types, like `live_sample_list`: "wav,aif".
            similar_to: A sample (path from a result, or a unique file name): rank by how close
                each sample sounds to it, using the audio features Live computed for its
                browser (closest first, `distance` 0 = the same sound). Combines with every
                filter ("kicks in my own folders that sound like this one"). `order` is
                ignored.
            include_copies: With `similar_to`, also list files with exactly the same audio
                as the seed (skipped by default).
            order: "relevance" (default: query words in the file name first, short names
                first), "name", "newest" (file date), "most_used" (how often Live loaded
                the file — the user's go-to sounds) or "random" (variety; same `seed` =
                same order).
            seed: Seed for order="random".
            offset, limit: Paging (limit 1..200, default 20).
            searches: Several searches in one call — a list of objects with the same keys as
                above (query, tags, any_tags, exclude_tags, kind, place, source, category,
                extensions, similar_to, include_copies, order, seed, offset, limit), max 16.
                Arguments given next to `searches` apply to every search unless it sets its
                own. Use it to pull a whole kit at once: [{"tags": ["Drums|Kick"]}, {"tags":
                ["Drums|Snare"]}, {"tags": ["Drums|Hihat"]}]. Returns `{results:[{search,
                ...the usual result or error}]}`.
            refresh: Rebuild the index from Live's database first (otherwise automatic when
                Live's database changed and the index is older than 5 minutes).

        Returns:
            `{total, offset, count, samples:[{name, path (absolute; null when the place's
            folder is unknown), place, source, rel_path, tags, kind, size?, use_count?,
            distance? (similar_to), exists}], next_offset?, seed? (similar_to), index:
            {refreshed?, stale?, unresolved_places?}}`.

        Gotchas: the first call builds the index (copies Live's database, a few seconds;
        nothing in Live is touched). `exists: false` means the file moved since Live indexed
        it. A null `path` means the place's folder is unknown — see `live_sample_index`. Tags
        are Live's own browser tags; files Live has not analysed yet have none.
        """
        cmd = "sample_index.search"
        spec = {"query": query, "tags": tags, "any_tags": any_tags,
                "exclude_tags": exclude_tags, "kind": kind, "place": place, "source": source,
                "category": category, "extensions": extensions, "similar_to": similar_to,
                "include_copies": include_copies, "order": order, "seed": seed,
                "offset": offset, "limit": limit}
        if searches is not None:
            if not isinstance(searches, list) or not 1 <= len(searches) <= _MAX_SEARCHES:
                return tool_error(f"searches must list 1..{_MAX_SEARCHES} search objects",
                                  cmd=cmd)
            shared = {k: v for k, v in spec.items() if v != _SEARCH_DEFAULTS[k]}
            merged = []
            for index, item in enumerate(searches):
                if not isinstance(item, dict):
                    return tool_error(f"searches[{index}] must be an object", cmd=cmd)
                unknown = set(item) - set(_SEARCH_DEFAULTS)
                if unknown:
                    return tool_error(f"searches[{index}]: unknown keys {sorted(unknown)} "
                                      f"(use {', '.join(_SEARCH_DEFAULTS)})", cmd=cmd)
                full = {**_SEARCH_DEFAULTS, **shared, **item}
                error = check_search(full, cmd, f"searches[{index}]: ")
                if error:
                    return error
                merged.append((item, full))
        else:
            error = check_search(spec, cmd, "")
            if error:
                return error
        state = ensure(refresh, cmd)
        if _is_error(state):
            return state
        if searches is not None:
            result: dict[str, Any] = {"results": [
                dict(run_search(full, cmd), search=item) for item, full in merged]}
        else:
            result = run_search(spec, cmd)
            if _is_error(result):
                return result
        if state["index"]:
            result["index"] = state["index"]
        return result

    @mcp.tool()
    def live_sample_duplicates(
        query: str | None = None,
        place: str | list[str] | None = None,
        source: str | list[str] | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Any:
        """Find samples whose audio is identical (the same sound shipped by several packs, or
        copied into several folders) — according to the audio fingerprint Live keeps in its
        browser database. Read-only: nothing is moved or deleted.

        Args:
            query: Only samples whose name or folders contain these words.
            place: Only these places; source: only these kinds of place (see
                `live_sample_search`). A group counts when at least two of its files match.
            offset, limit: Paging over the groups (largest first; limit 1..200).

        Returns:
            `{total_groups, duplicate_files (files beyond the first of each group), offset,
            count, groups:[{copies, samples:[{name, path, place, source, ...}]}],
            next_offset?, index?}`.
        """
        cmd = "sample_index.duplicates"
        if offset < 0 or not 1 <= limit <= 200:
            return tool_error("offset must be >= 0 and limit 1..200", cmd=cmd)
        state = ensure(False, cmd)
        if _is_error(state):
            return state
        try:
            result = sample_db.duplicates(_index_path(), query=query, places=_list(place),
                                          sources=_list(source), offset=offset, limit=limit)
        except sample_db.SampleIndexError as exc:
            return tool_error(str(exc), type="not_found", cmd=cmd)
        except sqlite3.DatabaseError as exc:
            return tool_error(f"the sample index is unreadable ({exc}) — rebuild it with "
                              "live_sample_index(action='refresh')", type="invalid_state",
                              cmd=cmd)
        if state["index"]:
            result["index"] = state["index"]
        return result

    @mcp.tool()
    def live_sample_tags(group: str | None = None, filter: str | None = None,
                         limit: int = 100, categories: bool = False) -> Any:
        """The tag vocabulary of Live's browser (as used in `live_sample_search`), with how many
        samples carry each tag — to turn "punchy", "dusty", "808" into real tags. Read-only.

        Args:
            group: One group's tags ("Drums", "Character", "Sounds", "Type", "Genres", "Key").
                Omitted: every group with its most used tags.
            filter: Only tags containing this text ("punch", "kick").
            limit: Max entries (1..500).
            categories: List Live's browser sound categories instead (the left-hand
                "Sounds" / "Drums" ... folders), with their file counts and the words that put
                a sample in them; `group` / `filter` narrow them. Use the names as
                `live_sample_search(category=...)`.

        Returns:
            `{groups:[{group, files, top:[{tag, files}]}]}`, `{tags:[{tag, files}]}` or
            `{categories:[{group, categories:[{category, files, words}]}]}`.
        """
        cmd = "sample_index.tags"
        if not 1 <= limit <= 500:
            return tool_error("limit must be 1..500", cmd=cmd)
        state = ensure(False, cmd)
        if _is_error(state):
            return state
        try:
            if categories:
                return sample_db.category_counts(_index_path(), group=group, filter=filter)
            return sample_db.tag_counts(_index_path(), group=group, filter=filter, limit=limit)
        except (sample_db.SampleIndexError, sqlite3.DatabaseError) as exc:
            return tool_error(str(exc), type="invalid_state", cmd=cmd)

    @mcp.tool()
    def live_plugin_search(
        query: str | None = None,
        format: str | None = None,
        kind: str | None = None,
        vendor: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Any:
        """Every plug-in installed on this machine that Live knows (its Plugins browser),
        not only the ones loaded in the set (`live_plugin_list`) — so you can tell what the
        user owns before suggesting or loading a plug-in. Read-only.

        Args:
            query: Words in the plug-in or vendor name ("serum", "valhalla", "comp").
            format: "VST3", "VST2", "AU" or "CLAP".
            kind: "instrument", "audio_effect" or "midi_effect".
            vendor: Part of the vendor name ("Xfer", "FabFilter").
            offset, limit: Paging (limit 1..500).

        Returns:
            `{total, offset, count, plugins:[{name, vendor, format, kind, device_id}],
            formats:{format: count}, next_offset?}`.

        Gotchas: load one with `live_browser_load(query=<name>, category="plugin",
        plugin_format=<format>)`, or for full parameter control of a VST3
        `live_plugin_expose(plugin=<name>)`. Prefer VST3 when a plug-in is installed in several
        formats. The list comes from Live's browser database (refreshed with the sample
        index): plug-ins Live has not scanned yet, or that are disabled in Live's settings,
        are missing.
        """
        cmd = "sample_index.plugins"
        if kind is not None and kind not in _PLUGIN_KINDS:
            return tool_error(f"kind must be one of {', '.join(_PLUGIN_KINDS)}", cmd=cmd)
        if offset < 0 or not 1 <= limit <= 500:
            return tool_error("offset must be >= 0 and limit 1..500", cmd=cmd)
        state = ensure(False, cmd)
        if _is_error(state):
            return state
        try:
            result = sample_db.plugin_search(_index_path(), query=query, format=format,
                                             kind=kind, vendor=vendor, offset=offset,
                                             limit=limit)
        except (sample_db.SampleIndexError, sqlite3.DatabaseError) as exc:
            return tool_error(str(exc), type="invalid_state", cmd=cmd)
        if state["index"]:
            result["index"] = state["index"]
        return result

    @mcp.tool()
    def live_sample_index(action: str = "status") -> Any:
        """Show or rebuild the sample search index (`live_sample_search`). Use "refresh" after
        adding folders or samples to Live's browser or re-tagging them, or when paths are
        missing. Nothing in Live is touched: the index is built from a temporary copy of
        Live's browser database.

        Args:
            action: "status" (default) or "refresh".

        Returns:
            `{built, index, source_db, built_at, build_seconds, samples, tagged, with_path,
            with_features (samples with Live's audio features, for similar_to), places:[{name,
            kind, root, root_from ("config"|"database"|"live"|"guess"), verified, samples,
            without_path?}], unresolved:[places with samples that have no path], stale?,
            refreshed?}`.

        Gotchas: a place is "unresolved" when its folder could not be found and checked; add
        it to ~/.livebridge/config.json — `"sample_index": {"roots": {"<place name>":
        "<folder>"}}` — then refresh. Run "refresh" with Live open so Live can report the
        Core Library and User Library folders.
        """
        cmd = "sample_index.status"
        if action not in _ACTIONS:
            return tool_error(f"action must be one of {', '.join(_ACTIONS)}", cmd=cmd)
        state = ensure(action == "refresh", cmd)
        if _is_error(state):
            return state
        settings = _settings()
        override_db = os.environ.get("LIVEBRIDGE_LIVE_DATABASE") or settings.get("database")
        database = sample_db.find_live_database(override_db)
        status = sample_db.index_status(
            _index_path(), sample_db.source_signature(database) if database else None)
        status.update({k: v for k, v in state["index"].items()
                       if k in ("refreshed", "live_reachable")})
        if status.get("unresolved"):
            status["hint"] = ('add the folders to ~/.livebridge/config.json: "sample_index": '
                              '{"roots": {"<place name>": "<folder>"}}, then '
                              "live_sample_index(action='refresh')")
        return status
