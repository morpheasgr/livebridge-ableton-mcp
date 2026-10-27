"""The skill and its companion files stay true to the tools.

`.claude/skills/livebridge/*.md` is what Claude reads before it calls the tools. Every `live_*`
name in them must be a real tool, and every call written as `live_x(name=..., ...)` (inline or in
a code block) may only pass arguments that tool accepts, so a renamed tool or argument breaks
this test instead of misleading Claude. Each companion is also served as an MCP resource.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "mcp_server") not in sys.path:
    sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp import server as server_mod  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

_APP = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=1.0))
TOOLS = {tool.name: tool for tool in asyncio.run(_APP.list_tools())}
SKILL_DIR = REPO / ".claude" / "skills" / "livebridge"
DOCS = sorted(SKILL_DIR.glob("*.md"))


def accepted_arguments(tool: str) -> set[str]:
    schema = getattr(TOOLS[tool], "input_schema", None) \
        or getattr(TOOLS[tool], "inputSchema", None) or {}  # mcp 2.x / 1.x
    return set(schema.get("properties", {}))


def doc_calls(text: str) -> list[tuple[int, str, list[str]]]:
    """(line, tool, keyword names) of every `live_x(...)` call with its arguments written out.

    The argument list runs to the matching parenthesis (it may span lines); keyword names are
    the `name=` at the top nesting level. `live_x(...)` and `live_x()` carry no keywords.
    """
    found = []
    for match in re.finditer(r"\b(live_[a-z_]+)\(", text):
        depth, index, quote = 1, match.end(), None
        while index < len(text) and depth:
            char = text[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            index += 1
        inner = text[match.end():index - 1]
        top, depth, quote = [], 0, None  # the argument text with nested brackets removed
        for char in inner:
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif depth == 0:
                top.append(char)
        keywords = re.findall(r"(?:^|,)\s*([a-z_][a-z0-9_]*)\s*=(?!=)", "".join(top))
        found.append((text.count("\n", 0, match.start()) + 1, match.group(1), keywords))
    return found


def test_skill_folder_holds_the_companions():
    names = {path.name for path in DOCS}
    assert {"SKILL.md", "PRODUCTION.md", "GENRES.md", "SERUM.md", "WORKFLOWS.md"} <= names
    for file_name, *_ in server_mod.COMPANIONS:
        assert file_name in names, file_name


def test_skill_docs_name_only_real_tools():
    problems = []
    for path in DOCS:
        for mention in set(re.findall(r"\blive_[a-z_]+", path.read_text(encoding="utf-8"))):
            if mention.endswith("_"):  # a family such as `live_simpler_*`
                if not any(tool.startswith(mention) for tool in TOOLS):
                    problems.append(f"{path.name}: no tool starts with {mention}")
            elif mention not in TOOLS:
                problems.append(f"{path.name}: no tool {mention}")
    assert not problems, "\n".join(sorted(problems))


def test_skill_doc_calls_use_real_arguments():
    calls = 0
    problems = []
    for path in DOCS:
        for line, tool, keywords in doc_calls(path.read_text(encoding="utf-8")):
            if tool not in TOOLS:
                continue  # reported by the test above
            calls += 1
            accepted = accepted_arguments(tool)
            problems += [f"{path.name}:{line}: {tool} has no argument {kw!r}"
                         for kw in keywords if kw not in accepted]
    assert calls >= 100, calls
    assert not problems, "\n".join(problems)


def test_doc_call_parser_handles_nesting_and_strings():
    text = ('`live_x(a=1, values={"b": "x=1", "c": [1, 2]}, d=f(e=2))` and\n'
            'live_y(\n    track="A, B",\n    notes=[["C1", 0]])')
    assert doc_calls(text) == [(1, "live_x", ["a", "values", "d"]),
                               (2, "live_y", ["track", "notes"])]


def test_companions_are_served_as_resources():
    resources = {str(r.uri) for r in asyncio.run(_APP.list_resources())}
    for file_name, uri, *_ in server_mod.COMPANIONS:
        assert uri in resources, uri
        contents = asyncio.run(_APP.read_resource(uri))
        text = "".join(getattr(c, "content", None) or getattr(c, "text", "") for c in contents)
        assert text.startswith("# "), (uri, text[:80])
        assert text == (SKILL_DIR / file_name).read_text(encoding="utf-8-sig")
