"""Surge XT patch files: read and write modulation routings, step sequences and parameters.

The writer copies a template ``.fxp`` (default: Surge's "Init Saw"), changes it and writes a
new patch into the user's Surge patches folder, where Surge's own browser lists it. See
``livebridge_mcp.surge`` and ``docs/SURGE_FORMAT.md``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import tool_error
from .. import surge
from ..client import BridgeClient

_TEMPLATE = "Init Saw.fxp"


def user_patches() -> Path:
    """Surge XT's user patch folder (Documents/Surge Synth Team/Surge XT/Patches)."""
    return Path.home() / "Documents" / "Surge Synth Team" / "Surge XT" / "Patches"


def _template_candidates() -> list[Path]:
    places = [user_patches()]
    if os.name == "nt":
        for var in ("PROGRAMDATA", "LOCALAPPDATA"):
            if os.environ.get(var):
                places.append(Path(os.environ[var]) / "Surge XT" / "patches_factory")
    else:
        places += [Path("/Library/Application Support/Surge XT/patches_factory"),
                   Path.home() / "Library/Application Support/Surge XT/patches_factory",
                   Path("/usr/share/surge-xt/patches_factory")]
    return places


def find_template() -> Path | None:
    for place in _template_candidates():
        if not place.is_dir():
            continue
        direct = place / "Templates" / _TEMPLATE
        if direct.is_file():
            return direct
        for hit in place.rglob(_TEMPLATE):
            return hit
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the Surge XT patch tools on the MCP app."""

    @mcp.tool()
    def live_surge_routings(file_path: str) -> Any:
        """List a Surge XT patch's modulation routings and step sequences. Reads the file only.

        Args:
            file_path: The `.fxp` (Surge's patches: Documents/Surge Synth Team/Surge XT/Patches)
                or a Surge `.vstpreset`.

        Returns:
            `{file_path, name, revision, routings: [{target, source, depth, output?, muted?}],
            step_sequences: [{scene, lfo, steps (16, -1..1), loop: [first, last], shuffle}],
            sources: [names]}`. depth is in the target's units: semitones for pitch and
            filter cutoff, 1.0 = 100 % for levels and other % targets.
        """
        cmd = "surge.routings"
        try:
            path = Path(file_path).expanduser()
            patch = surge.Patch.load(path)
            return {"file_path": str(path), "name": patch.name, "revision": patch.revision,
                    "routings": patch.routings(), "step_sequences": patch.step_sequences(),
                    "sources": list(surge.SOURCES)}
        except FileNotFoundError:
            return tool_error(f"no such file: {file_path}", type="not_found", cmd=cmd)
        except Exception as exc:
            return tool_error(str(exc)[:1000], cmd=cmd)

    @mcp.tool()
    def live_surge_write(
        name: str,
        routings: list[dict[str, Any]] | None = None,
        step_sequences: list[dict[str, Any]] | None = None,
        parameters: dict[str, float] | None = None,
        template: str | None = None,
        output_path: str | None = None,
        formats: list[str] | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> Any:
        """Write a new Surge XT patch: a copy of the template with routings, step sequences and
        parameters changed; everything else kept (wavetables, FX, nightly data). Written to
        Surge's user folder (Patches/LiveBridge/<name>.fxp, for Surge's own browser) and as
        <name>.vstpreset beside it — load that into Live with live_plugin_expose(plugin=
        "Surge XT", preset_file=<vstpreset_path>, ...) or drag it from Live's browser.

        Args:
            name: The patch name (file name and the name Surge shows).
            routings: [{"target": "a_filter1_cutoff", "source": "lfo1", "depth": 12}] —
                targets as named in the patch (a_ = scene A, b_ = scene B: a_osc1_pitch,
                a_filter1_resonance, a_pan, a_volume, fx1_p0 ...); sources velocity,
                keytrack, polyat, aftertouch, pitchbend, modwheel, macro1-8, ampeg, filtereg,
                lfo1-6, slfo1-6, releasevelocity, lowestkey, highestkey; depth in the target's
                units (semitones for pitch and cutoff, 1.0 = 100 %), or "remove": true.
            step_sequences: [{"lfo": 1, "steps": [1, 0, 0.5, ...], "loop": [1, 8],
                "shuffle": 0.2, "scene": "a"}] — lfo 1-6 = voice LFOs, 7-12 = scene LFOs
                (S-LFO 1-6); steps -1..1 (0 = middle), up to 16; sets the LFO's shape to step
                sequencer. Route that LFO somewhere to hear it.
            parameters: {"a_filter1_cutoff": -12.0, "a_osc1_type": 0} — raw patch values in
                the patch's own units (read an existing patch to see them).
            template: The `.fxp` or Surge `.vstpreset` to start from (default: Surge's
                "Init Saw").
            output_path: Full path (.fxp or .vstpreset) instead of Patches/LiveBridge/<name>;
                the other format is written beside it with the same name.
            formats: ["fxp", "vstpreset"] (default both).
            dry_run: Report the changes, write nothing.
            overwrite: Replace an existing output file (never the template).

        Returns:
            `{template, output_path, vstpreset_path?, changes, routings, step_sequences,
            warnings?, written}` — warnings name what will keep a change from being heard
            (a routed filter that is Off, a free-running step sequencer).

        Gotchas: loading the .vstpreset with live_plugin_expose resets the EXPOSED parameters
        to the template's values (Surge applies the preset after Live reads them): expose few
        and set them again, or use live_plugin_host_load + live_plugin_host_preset. Init
        Saw's filters are Off. The arpeggiator (Surge nightlies) is kept but not written. A depth of 0 is
        refused (Surge drops such routings). Parameter values are written as given — copy them
        from patches that sound right.
        """
        cmd = "surge.write"
        routings, step_sequences, parameters = routings or [], step_sequences or [], \
            parameters or {}
        if not (routings or step_sequences or parameters):
            return tool_error("give routings, step_sequences and/or parameters", cmd=cmd)
        stem = (name or "").strip().removesuffix(".fxp")
        if not stem or any(c in stem for c in '\\/:*?"<>|'):
            return tool_error("name must be a plain patch name", cmd=cmd)
        try:
            source = Path(template).expanduser() if template else find_template()
            if source is None:
                return tool_error("Surge's 'Init Saw' was not found — pass template= (any "
                                  ".fxp), e.g. one saved from Surge", type="not_found", cmd=cmd)
            patch = surge.Patch.load(source)
            changes: list[dict[str, Any]] = []
            for key, value in parameters.items():
                changes.append(patch.set_parameter(key, value))
            for entry in step_sequences:
                if not isinstance(entry, dict) or "lfo" not in entry or "steps" not in entry:
                    return tool_error("each step sequence needs lfo and steps", cmd=cmd)
                changes.append(patch.set_step_sequence(int(entry["lfo"]), entry["steps"],
                                                       scene=entry.get("scene", "a"),
                                                       loop=entry.get("loop"),
                                                       shuffle=entry.get("shuffle")))
            for entry in routings:
                if not isinstance(entry, dict) or "target" not in entry or "source" not in entry:
                    return tool_error("each routing needs target and source", cmd=cmd)
                changes.append(patch.route(entry["target"], entry["source"],
                                           depth=entry.get("depth"),
                                           remove=bool(entry.get("remove")),
                                           output=int(entry.get("output", 0))))
            patch.set_name(stem)
            kinds = [k.lower().lstrip(".") for k in (formats or ["fxp", "vstpreset"])]
            if not kinds or any(k not in ("fxp", "vstpreset") for k in kinds):
                return tool_error("formats must list fxp and/or vstpreset", cmd=cmd)
            base = Path(output_path).expanduser() if output_path else \
                user_patches() / "LiveBridge" / f"{stem}.fxp"
            if base.suffix.lower() not in (".fxp", ".vstpreset"):
                return tool_error("output_path must end in .fxp or .vstpreset", cmd=cmd)
            outputs = {k: (base.with_suffix("." + k), patch.to_bytes() if k == "fxp"
                           else patch.to_vstpreset()) for k in kinds}
            for _path, data in outputs.values():
                surge.Patch(data)                             # what we write reads again
            for path, _data in outputs.values():
                if path.exists() and path.resolve() == source.resolve():
                    return tool_error("the output would replace the template", cmd=cmd)
            result: dict[str, Any] = {"template": str(source), "changes": changes,
                                      "routings": patch.routings(),
                                      "step_sequences": patch.step_sequences(),
                                      "written": False}
            warnings = patch.warnings()
            if warnings:
                result["warnings"] = warnings
            if "fxp" in outputs:
                result["output_path"] = str(outputs["fxp"][0])
            if "vstpreset" in outputs:
                result["vstpreset_path"] = str(outputs["vstpreset"][0])
            if dry_run:
                return result
            taken = [str(p) for p, _d in outputs.values() if p.exists()]
            if taken and not overwrite:
                return tool_error(f"{', '.join(taken)} exists (pass overwrite=true or another "
                                  "name)", type="invalid_state", cmd=cmd)
            for path, data in outputs.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            result["written"] = True
            return result
        except FileNotFoundError as exc:
            return tool_error(f"no such file: {exc.filename or template}", type="not_found",
                              cmd=cmd)
        except Exception as exc:
            return tool_error(str(exc)[:1000], cmd=cmd)
