"""Try the sample search from a terminal, without Claude — the same tools Claude calls.

    python -m livebridge_mcp.sample_check                     # status (builds the index if needed)
    python -m livebridge_mcp.sample_check --refresh           # rebuild from Live's database
    python -m livebridge_mcp.sample_check --tags "Drums|Kick" "Character|Punchy" --kind one_shot
    python -m livebridge_mcp.sample_check --query "909 kick" --limit 5
    python -m livebridge_mcp.sample_check --list-tags Character

Uses the same settings as the MCP server (``~/.livebridge/config.json``); Live should be open
so it can report its Core Library and User Library folders during a refresh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Sequence

from .client import BridgeClient
from .config import load_config


def _call(app: Any, name: str, args: dict[str, Any]) -> Any:
    result = asyncio.run(app.call_tool(name, args))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    text = "\n".join(b.text for b in content if getattr(b, "type", None) == "text")
    try:
        return json.loads(text)
    except ValueError:
        return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m livebridge_mcp.sample_check",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--refresh", action="store_true", help="rebuild the index first")
    parser.add_argument("--query", help="words in the name or folders")
    parser.add_argument("--tags", nargs="+", help='tags that must all be present ("Drums|Kick")')
    parser.add_argument("--kind", choices=("one_shot", "loop"))
    parser.add_argument("--place", nargs="+", help="only these places")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--list-tags", nargs="?", const="", metavar="GROUP",
                        help="list tag groups, or the tags of one group")
    args = parser.parse_args(argv)

    from .server import create_app

    config = load_config()
    bridge = BridgeClient.from_config(config)
    app = create_app(bridge, config=config)
    try:
        if args.list_tags is not None:
            answer = _call(app, "live_sample_tags",
                           {"group": args.list_tags} if args.list_tags else {})
        elif args.query or args.tags or args.kind or args.place:
            search: dict[str, Any] = {"limit": args.limit, "refresh": args.refresh}
            for key in ("query", "tags", "kind", "place"):
                if getattr(args, key):
                    search[key] = getattr(args, key)
            answer = _call(app, "live_sample_search", search)
        else:
            answer = _call(app, "live_sample_index",
                           {"action": "refresh" if args.refresh else "status"})
    finally:
        bridge.close()
    print(json.dumps(answer, indent=1, ensure_ascii=False))
    return 1 if isinstance(answer, dict) and "error" in answer else 0


if __name__ == "__main__":
    sys.exit(main())
