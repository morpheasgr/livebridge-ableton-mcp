# Sample search with Live's browser tags

`live_sample_search` searches everything Live's browser shows under **Library > Samples**:
the Core Library, packs, the User Library and every folder you added to Places, with Live's
own browser tags (`Drums|Kick`, `Character|Punchy`, `Type|One Shot`, `Sounds|Bass`, ...).
Live's Python API lists only part of this (`browser.samples`: Core Library, packs, User
Library) and no tags; Live's browser database has all of it.

| Tool | What |
|---|---|
| `live_sample_search` | words (`query`), tags (`tags` all / `any_tags` / `exclude_tags`), `kind` (`one_shot` / `loop`), `place`, `extensions`, `order` (`relevance`, `name`, `newest`, `random`), paging; every result has its absolute `path` |
| `live_sample_tags` | the tag groups and tags with how many samples carry them |
| `live_sample_index` | status (places, their folders, what is unresolved) or `action="refresh"` |

A result loads with the existing tools: `live_drumrack_set_pad(note="C1", file_path=...)`,
`live_sample_import(file_path=...)`, `live_simpler_action("replace_sample", file_path=...)`.

Without Claude, from a terminal (same tools, same settings):

```
python -m livebridge_mcp.sample_check --refresh
python -m livebridge_mcp.sample_check --tags "Drums|Kick" "Character|Punchy" --kind one_shot
python -m livebridge_mcp.sample_check --query "909 kick" --limit 5
python -m livebridge_mcp.sample_check --list-tags Character
```

## How it works

1. **Find Live's database**: `Live-files-<N>.db` with the highest N in
   `%LOCALAPPDATA%\Ableton\Live Database` (Windows) or
   `~/Library/Application Support/Ableton/Live Database` (macOS).
2. **Copy, never open**: the database and its `-wal` file are copied together into a temporary
   folder; SQLite reads the copy, the copy is deleted. Live's files are only read as bytes, so
   Live's database cannot be changed or locked. If Live wrote during the copy and the copy is
   unreadable, it is copied once more.
3. **Rebuild paths**: `files` rows are walked up `parent_id` to their place's root folder;
   only audio files (`.wav .aif .aiff .mp3 .flac .ogg .m4a`) are kept, with their tags from
   `metadata` + `metadata_values`.
4. **Find each place's folder** (the database stores paths relative to it), in this order, and
   accept a folder only when some of that place's files really exist under it:
   1. `sample_index.roots` in `~/.livebridge/config.json` (your setting);
   2. an absolute path stored in the database for that place;
   3. what Live reports (`samples.locations`): the Core Library next to the running Live
      executable, the User Library and Factory Packs from Live's `Library.cfg`, the known
      Music / Downloads / Desktop folders;
   4. folders of the same name: `<drive>:\<place>` (e.g. `D:\Samples`), your home folder,
      Documents, Music, Desktop, `Documents\Ableton`, the default Live install folders.
5. **Index**: everything goes into `~/.livebridge/sample_index.sqlite`; searches take a few
   milliseconds. All of this runs in the MCP server process — nothing runs inside Live except
   the one quick `samples.locations` question during a refresh.

**Refresh**: on a search, when Live's database (or its -wal) changed and the index is older
than `auto_refresh_seconds` (default 300), it is rebuilt; otherwise results carry
`index.stale: true`. `live_sample_index(action="refresh")` rebuilds at once — do it with Live
open, so Live can report the Core Library and User Library folders.

## Settings

All optional, in `~/.livebridge/config.json` (the installer keeps them):

```json
"sample_index": {
  "roots": {"Samples": "D:\\Samples", "Projects": "D:\\Projects"},
  "database": "C:\\Users\\you\\AppData\\Local\\Ableton\\Live Database",
  "auto_refresh_seconds": 300
}
```

* `roots`: place name (as Live's browser shows it) -> folder, for places that stay
  unresolved. A folder that fails the check is not used.
* `database`: a `Live-files-<N>.db` file or the folder holding them (also
  `LIVEBRIDGE_LIVE_DATABASE`).
* `auto_refresh_seconds`: `0` = refresh only on request.

## Troubleshooting

* `live_sample_index()` lists every place with its folder, where it came from (`config`,
  `database`, `live`, `guess`) and `unresolved` places: add those to `roots`, then refresh.
* `exists: false` on a result: the file moved after Live indexed it; Live's browser shows it
  as missing too until it rescans the folder.
* LAN mode (Live on another computer): the database is on the Live computer, so the search
  runs only where the MCP server can read it.
* Live's database layout is not documented by Ableton. If a Live update changes it, the tools
  answer "unexpected layout" instead of guessing.
