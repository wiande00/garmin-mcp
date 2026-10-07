"""The MCP surface: which tools are registered, how, and that failures arrive intact."""

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp import server

from .fakes import FakeGarmin, make_cfg


def tools_of(srv) -> dict:
    return {t.name: t for t in anyio.run(srv.list_tools)}


def call(srv, name, **args):
    result = anyio.run(lambda: srv.call_tool(name, args))
    return result


def text_of(result) -> str:
    return "".join(c.text for c in result.content)


class TestRegistration:
    def test_every_tool_is_named_garmin_and_annotated(self, tmp_path):
        tools = tools_of(server.build_server(make_cfg(tmp_path)))
        assert "garmin_status" in tools
        for name, t in tools.items():
            assert name.startswith("garmin_")
            assert t.annotations is not None and t.annotations.read_only_hint is not None

    def test_tools_never_send_their_answer_twice(self, tmp_path):
        # structuredContent would repeat the JSON already in the text (taxuspt #331).
        tools = tools_of(server.build_server(make_cfg(tmp_path)))
        assert all(t.output_schema is None for t in tools.values())

    def test_read_only_mode_drops_exactly_the_tools_that_change_garmin(self, tmp_path):
        ro = tools_of(server.build_server(make_cfg(tmp_path, GARMIN_MCP_READ_ONLY="1")))
        kinds = {fn.__name__: kind for fn, kind in server.TOOLS}
        assert set(ro) == {n for n, k in kinds.items() if k not in server.CHANGES_GARMIN}

    def test_annotations_match_what_each_kind_of_tool_does(self, tmp_path):
        tools = tools_of(server.build_server(make_cfg(tmp_path)))
        kinds = {fn.__name__: kind for fn, kind in server.TOOLS}
        for name, t in tools.items():
            a = t.annotations
            assert a.read_only_hint is (kinds[name] == "read")
            assert bool(a.destructive_hint) is (kinds[name] == "delete")

    def test_an_allow_list_registers_only_those_tools(self, tmp_path):
        cfg = make_cfg(tmp_path, GARMIN_MCP_ENABLED_TOOLS="garmin_status")
        assert set(tools_of(server.build_server(cfg))) == {"garmin_status"}

    def test_an_allow_list_naming_nothing_known_stops_startup(self, tmp_path):
        cfg = make_cfg(tmp_path, GARMIN_MCP_ENABLED_TOOLS="get_sleep_data")
        with pytest.raises(SystemExit):
            server.build_server(cfg)

    def test_a_deny_list_removes_tools(self, tmp_path):
        cfg = make_cfg(tmp_path, GARMIN_MCP_DISABLED_TOOLS="garmin_status")
        assert "garmin_status" not in tools_of(server.build_server(cfg))


class TestStatus:
    def test_it_explains_how_to_log_in_when_there_is_no_login(self, tmp_path):
        srv = server.build_server(make_cfg(tmp_path))
        out = text_of(call(srv, "garmin_status"))
        assert "Not logged in" in out and "garmin-mcp-auth login" in out

    def test_it_names_the_account_and_the_last_sync(self, tmp_path):
        cfg = make_cfg(tmp_path)
        cfg.home.mkdir(parents=True)
        cfg.token_file.write_text('{"di_token": "x", "di_refresh_token": "y"}', encoding="utf-8")
        fake = FakeGarmin(
            {
                "get_device_last_used": {
                    "lastUsedDeviceName": "Forerunner 965",
                    "lastUsedDeviceUploadTime": 1791360000000,
                }
            }
        )
        srv = server.build_server(cfg, opener=lambda: (fake, None))
        out = text_of(call(srv, "garmin_status"))
        assert "Logged in to Garmin Connect as Test Runner." in out
        assert "Forerunner 965" in out


class TestFailures:
    def test_a_garmin_failure_reaches_the_model_as_a_tool_error(self, tmp_path):
        server.build_server(make_cfg(tmp_path))

        async def failing():
            return await server._call(lambda ctx: ctx.g.get_stats("2026-10-06"))

        with pytest.raises(ToolError, match="Not logged in"):
            anyio.run(failing)
