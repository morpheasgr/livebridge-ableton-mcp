# Third-party notices

`GENRES.md`, `WORKFLOWS.md`, `PRODUCTION.md` §11-12, `SERUM.md` §6, `SERUM_PRESETS.md` and
`OMNISPHERE.md` adapt production know-how from MIT-licensed projects. The text
was rewritten for LiveBridge's tools; the musical content (patterns, patch settings, mixing
values, procedures) comes from these sources:

- **ableton-mcp-pro** by nicholasbien, https://github.com/nicholasbien/ableton-mcp-pro
  (`.claude/skills/*`, commit 1a66f93): genre drum, bass, chord and sound-design skills, drum
  swing, mix check, mixing guide and track arrangement. Used in `GENRES.md` and `WORKFLOWS.md` §9.
- **ableton-skills** by Glincker, https://github.com/glincker/ableton-skills
  (`skills/*`, `commands/*`, commit 347cd7d): arrangement coach, chord pro, groove builder,
  mastering prep, MIDI cleanup, mixer doctor, producer mode, reference match, side-chain setup,
  sound designer, tempo coach, vocal chain and the init/export/snapshot commands. Used in
  `WORKFLOWS.md`.

- **ableton-mcp-pro, kasperikaivola's fork**, https://github.com/kasperikaivola/ableton-mcp-pro
  (commit 6ac32c8, same MIT license and copyright notice below): the Serum 2 preset guide
  (`MCP_Server/synth_guides/serum-presets.md` -> `SERUM_PRESETS.md`, adapted to LiveBridge's
  tools) and the Omnisphere guide (`omnisphere.md` -> `OMNISPHERE.md`, with a LiveBridge header);
  the Serum 2 control reference (`serum2.md` -> `SERUM.md` §6, condensed), the
  `arrangement-sections` skill (-> `WORKFLOWS.md` §16) and the synth sound-design skill with
  `synth_guides/overview.md` (-> `WORKFLOWS.md` §17, condensed);
  the code behind them is ported in `mcp_server/livebridge_mcp/serum/` (license copy there).

- **hellyee** by Güven Seçkin, https://github.com/guvense/hellyee (`.claude/skills/*`,
  `hellyee/core.py`, commit 159655e, MIT): melody craft and emotion-to-notes
  (-> `PRODUCTION.md` §11-12 and the melody check in `mcp_server/livebridge_mcp/theory.py`),
  arrangement transitions (-> `WORKFLOWS.md` §18), the sound check and the preset audit
  thresholds and filter floors (-> `WORKFLOWS.md` §19, `live_sound_check`), mastering
  guardrails (-> `WORKFLOWS.md` §20, `live_master_auto`) and the silent-failure list in
  `SKILL.md` §7. Rewritten for LiveBridge's tools; code ideas re-implemented, not copied.
- **ableton-auto-mix-mcp** by HighVoltSound, https://github.com/HighVoltSound/ableton-auto-mix-mcp
  (commit 5937cec, MIT): the 18 genre style profiles (loudness, band balance, role levels) and
  the track-or-master decision rule, re-implemented in `mcp_server/livebridge_mcp/mix.py`.

`SERUM.md` §1-5 are original to this fork, built on LiveBridge's Serum 2 parameter map.

## ableton-mcp-pro

MIT License

Copyright (c) 2026 nicholasbien

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## ableton-skills

MIT License

Copyright (c) 2026 Glincker

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## hellyee

MIT License

Copyright (c) 2026 Güven Seçkin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## ableton-auto-mix-mcp

MIT License

Copyright (c) 2026 HighVoltSound

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
