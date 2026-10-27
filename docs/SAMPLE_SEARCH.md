# Sample search with Live's browser tags

`live_sample_search` searches everything Live's browser shows under **Library > Samples**:
the Core Library, packs, the User Library and every folder you added to Places, with Live's
own browser tags (`Drums|Kick`, `Character|Punchy`, `Type|One Shot`, `Sounds|Bass`, ...).
Live's Python API lists only part of this (`browser.samples`: Core Library, packs, User
Library) and no tags; Live's browser database has all of it.

| Tool | What |
|---|---|
| `live_sample_search` | words (`query`), tags (`tags` all / `any_tags` / `exclude_tags`), `kind` (`one_shot` / `loop`), `place`, `source` (`core_library`, `pack`, `user_library`, `user_folder`, `current_project`), `extensions`, `order` (`relevance`, `name`, `newest`, `most_used`, `random`), `similar_to` (rank by how close each sample sounds to another), paging; every result has its absolute `path` |
| `live_sample_duplicates` | groups of samples with identical audio (the same sound shipped twice) |
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
python -m livebridge_mcp.sample_check --similar "D:\Samples\Kicks\Kick 01.wav"
python -m livebridge_mcp.sample_check --tags "Drums|Kick" --order most_used
python -m livebridge_mcp.sample_check --duplicates
```

## How it works

1. **Find Live's database**: `Live-files-<N>.db` with the highest N in
   `%LOCALAPPDATA%\Ableton\Live Database` (Windows) or
   `~/Library/Application Support/Ableton/Live Database` (macOS).
2. **Copy, never open**: the database and its `-wal` file are copied together into a temporary
   folder; SQLite reads the copy, the copy is deleted. Live's files are only read as bytes, so
   Live's database cannot be changed or locked. If Live wrote during the copy and the copy is
   unreadable, it is copied once more.
3. **Rebuild paths**: `files` keeps every folder up to the drive (`C:\` -> `ProgramData`
   -> `Ableton` -> ...), so each audio file (`.wav .aif .aiff .mp3 .flac .ogg .m4a`) gets
   its absolute path from the database, plus its path relative to its place. Tags are the
   `Group|Tag` values of `metadata` + `metadata_values` (other metadata — pack names, URLs,
   years — is left out). `files.use_count` is Live's own "how often loaded" counter;
   `places.folder_kind` says what each place is.
4. **Check each place's folder**, in this order, accepting a folder only when some of that
   place's files really exist under it (so a moved drive or a renamed folder can be
   corrected):
   1. `sample_index.roots` in `~/.livebridge/config.json` (your setting);
   2. the folder the database itself records for that place;
   3. what Live reports (`samples.locations`): the Core Library next to the running Live
      executable, the User Library and Factory Packs from Live's `Library.cfg`, the known
      Music / Downloads / Desktop folders;
   4. folders of the same name: `<drive>:\<place>` (e.g. `D:\Samples`), your home folder,
      Documents, Music, Desktop, `Documents\Ableton`, the default Live install folders.
5. **Similar sounds**: `fe_values` holds the audio features Live computes for its own
   "similar samples" browsing (a small header — version, dimension count — then 64 floats)
   and a hash of the audio. `similar_to` ranks samples by distance in that feature space,
   each feature scaled by its spread across your library; the same hash + features means
   identical audio (`live_sample_duplicates`). Live analyses new files in the background, so
   recently added samples may have no features yet.
6. **Index**: everything goes into `~/.livebridge/sample_index.sqlite`; searches take a few
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
