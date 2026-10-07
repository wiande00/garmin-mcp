"""PARITY.md must stay true: every Taxuspt tool maps to a real tool and real values."""

import re
from pathlib import Path

import anyio

from garmin_mcp import server

from .fakes import make_cfg

PARITY = Path(__file__).resolve().parents[1] / "PARITY.md"


def rows():
    """The data rows of the table under '## Tools'."""
    out, in_tools = [], False
    for line in PARITY.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_tools = line.strip() == "## Tools"
            continue
        if not in_tools or not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "Taxuspt tool":
            continue
        out.append((cells[0], cells[1], cells[2]))
    return out


def schemas(tmp_path):
    srv = server.build_server(make_cfg(tmp_path))
    return {t.name: t.input_schema for t in anyio.run(srv.list_tools)}


def allowed_values(schema: dict, param: str) -> set | None:
    prop = schema["properties"][param]
    for candidate in [prop, prop.get("items", {})] + prop.get("anyOf", []):
        if "enum" in candidate:
            return set(candidate["enum"])
        if "items" in candidate and "enum" in candidate["items"]:
            return set(candidate["items"]["enum"])
    return None


def test_all_153_taxuspt_tools_are_listed_once():
    names = [r[0] for r in rows()]
    assert len(names) == 153
    assert len(set(names)) == 153


def test_every_mapping_points_at_a_real_tool_and_real_values(tmp_path):
    tools = schemas(tmp_path)
    problems = []
    for taxuspt, ours, args in rows():
        if ours not in tools:
            problems.append(f"{taxuspt}: {ours} is not a registered tool")
            continue
        props = tools[ours].get("properties", {})
        for arg in filter(None, (a.strip() for a in args.split(","))):
            name, _, value = arg.partition("=")
            if name not in props:
                problems.append(f"{taxuspt}: {ours} has no parameter {name!r}")
            elif value:
                choices = allowed_values(tools[ours], name)
                if choices is not None and value not in choices:
                    problems.append(f"{taxuspt}: {ours}.{name} does not accept {value!r}")
    assert problems == []


def test_every_registered_tool_is_accounted_for(tmp_path):
    mapped = {r[1] for r in rows()}
    beyond = {"garmin_api_get"}  # tools with no Taxuspt counterpart
    assert set(schemas(tmp_path)) == mapped | beyond


def test_the_taxuspt_resources_exist(tmp_path):
    srv = server.build_server(make_cfg(tmp_path))
    uris = {str(r.uri) for r in anyio.run(srv.list_resources)}
    for uri in re.findall(r"\| (workout://\S+) \|", PARITY.read_text(encoding="utf-8")):
        assert uri in uris
