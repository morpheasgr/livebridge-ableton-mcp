"""Massive (classic) patch files: read and write the modulation routings Live cannot reach.

Live sets Massive's knobs, selectors and wavetables by name (live_plugin_expose /
live_device_set_parameters); which source modulates which knob lives only in the patch. These
tools copy a template ``.vstpreset`` (default: the user's "Massive Init (LB routed)"), change
its routings and write a new file to load with live_plugin_expose(preset_file=...). See
``livebridge_mcp.massive`` and the guide ``MASSIVE.md`` (resource ``livebridge://massive``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import bridge_call, tool_error
from .. import massive
from ..client import BridgeClient

_TEMPLATE_FILTER = "LB routed"


def _expand(path: str) -> Path:
    return Path(path).expanduser()


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the Massive patch tools on the MCP app."""

    def find_template() -> Path | dict[str, Any]:
        found = bridge_call(bridge, "plugin_racks.presets",
                            {"plugin": "Massive", "filter": _TEMPLATE_FILTER, "offset": 0,
                             "limit": 20, "embeddable_only": True}, timeout=30.0)
        if isinstance(found, dict) and "error" in found:
            return found
        for preset in (found or {}).get("presets") or []:
            if str(preset.get("path", "")).lower().endswith(".vstpreset"):
                return Path(preset["path"])
        return tool_error("no 'Massive Init (LB routed)' .vstpreset found — pass template=, "
                          "or save one: drag the Massive device into the User Library "
                          "(MASSIVE.md, Templates)", type="not_found", cmd="massive.write")

    @mcp.tool()
    def live_massive_routings(file_path: str | None = None) -> Any:
        """List the modulation routings in a Massive `.vstpreset` (which source drives which
        knob, how far) and the knobs that can be modulated. Reads the file only.

        Args:
            file_path: The `.vstpreset` (default: the "Massive Init (LB routed)" template
                found in the User Library).

        Returns:
            `{file_path, routings: [{target, source, amount, slot, semitones?}],
            modulatable: [knob names], sources: [names], slots: {5..8: {type, curve_a,
            curve_b} | {type: "stepper", steps}}}`. amount is -1..1, a fraction of the
            knob's range; on a pitch knob also in semitones (1.0 = 128).
        """
        cmd = "massive.routings"
        try:
            path = _expand(file_path) if file_path else find_template()
            if isinstance(path, dict):
                return path
            patch = massive.Patch.load(path)
            result = {"file_path": str(path), "routings": patch.routings(),
                      "modulatable": patch.targets(), "sources": list(massive.SOURCES)}
            try:
                result["slots"] = patch.slots()
            except ValueError as exc:
                result["slots_note"] = str(exc)
            return result
        except FileNotFoundError:
            return tool_error(f"no such file: {file_path}", type="not_found", cmd=cmd)
        except Exception as exc:
            return tool_error(str(exc)[:1000], cmd=cmd)

    @mcp.tool()
    def live_massive_write(
        routings: list[dict[str, Any]] | None = None,
        slots: list[dict[str, Any]] | None = None,
        name: str | None = None,
        output_path: str | None = None,
        template: str | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> Any:
        """Write a new Massive `.vstpreset`: a copy of the template with routings added,
        changed or removed; every other setting kept. Then load it with
        live_plugin_expose(plugin="Massive", preset_file=<output_path>, parameters=
        ["sound_design"]) and set knobs, wavetables and types through Live by name.

        Args:
            routings: 1..64 of {"target": knob name as in live_plugin_param_map
                ("FILTER1-CUT/PRM.1", "OSC1-POSITION", "INSERT1-DW/PRM.1" ...), "source":
                "env1".."env4" | "slot5".."slot8" (the LFO/stepper/performer slots) |
                "macro1".."macro8" | "ktr" | "vel" | "trr" | "at", "amount": -1..1 (fraction
                of the knob's range) or "semitones" (pitch knobs), or "remove": true}. A source
                already on the knob gets the new amount; otherwise it takes a free slot.
            slots: Mod slots 5-8: [{"slot": 5, "curve_a": "square", "curve_b": "triangle"},
                {"slot": 8, "type": "stepper", "steps": [1, 0, 0.5, 0, ...]}]. LFO curves:
                "sine" | "saw" (down) | "square" | "triangle"; the slot's XFade knob
                (MODn-CROSSF./GLIDE, set through Live) blends A (0) to B (100). type: "lfo" |
                "stepper"; steps: 1..16 values 0..1 (0 = bottom; steps not given stay 0).
                Rate, sync, restart and amp of the slot are set through Live as before.
            name: File name for the new preset (default "LB Massive"); written next to the
                template, so it shows in Live's browser beside it.
            output_path: Full path instead of name.
            template: The `.vstpreset` to start from (default: "Massive Init (LB routed)").
            dry_run: Report the changes, write nothing.
            overwrite: Replace an existing output file (never the template).

        Returns:
            `{template, output_path?, changes: [...], routings: [...], slots?, written}`.

        Gotchas: a knob has 1-3 slots (see live_massive_routings); a full knob is refused —
        remove a routing first. The modulation depth also follows the source's own level
        (envelope levels, MOD5-8 AMP, macro values): a routed source at rest is silent.
        The template's own routings stay unless removed. Performer slots and the stepper's
        per-step buttons (glide) are not written yet.
        """
        cmd = "massive.write"
        routings, slots = routings or [], slots or []
        if not isinstance(routings, list) or len(routings) > 64 or \
                not isinstance(slots, list) or len(slots) > 8 or not routings + slots:
            return tool_error("give routings (1..64) and/or slots (1..8)", cmd=cmd)
        try:
            source = _expand(template) if template else find_template()
            if isinstance(source, dict):
                return source
            patch = massive.Patch.load(source)
            changes = []
            for entry in routings:
                if not isinstance(entry, dict) or "target" not in entry or "source" not in entry:
                    return tool_error("each routing needs target and source", cmd=cmd)
                changes.append(patch.route(entry["target"], entry["source"],
                                           amount=entry.get("amount"),
                                           semitones=entry.get("semitones"),
                                           remove=bool(entry.get("remove"))))
            for entry in slots:
                if not isinstance(entry, dict) or "slot" not in entry:
                    return tool_error("each slots entry needs slot (5..8)", cmd=cmd)
                changes.append(patch.set_slot(int(entry["slot"]), type=entry.get("type"),
                                              curve_a=entry.get("curve_a"),
                                              curve_b=entry.get("curve_b"),
                                              steps=entry.get("steps")))
            data = patch.to_bytes()
            massive.Patch(data)                          # what we write parses again
            result: dict[str, Any] = {"template": str(source), "changes": changes,
                                      "routings": patch.routings(), "written": False}
            if slots:
                result["slots"] = patch.slots()
            if output_path:
                target = _expand(output_path)
            else:
                stem = (name or "LB Massive").strip().removesuffix(".vstpreset")
                if not stem or any(c in stem for c in '\\/:*?"<>|'):
                    return tool_error("name must be a plain file name", cmd=cmd)
                target = source.with_name(stem + ".vstpreset")
            if target.suffix.lower() != ".vstpreset":
                return tool_error("output_path must end in .vstpreset", cmd=cmd)
            if target.resolve() == source.resolve():
                return tool_error("the output would replace the template — choose another "
                                  "name", cmd=cmd)
            result["output_path"] = str(target)
            if dry_run:
                return result
            if target.exists() and not overwrite:
                return tool_error(f"{target} exists (pass overwrite=true or another name)",
                                  type="invalid_state", cmd=cmd)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            result["written"] = True
            return result
        except FileNotFoundError as exc:
            return tool_error(f"no such file: {exc.filename or template}", type="not_found",
                              cmd=cmd)
        except Exception as exc:
            return tool_error(str(exc)[:1000], cmd=cmd)
