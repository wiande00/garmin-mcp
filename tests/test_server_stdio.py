"""The real server, started the way Claude starts it: a subprocess speaking MCP over stdio."""

import sys
import time

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _session_run(tmp_path, job):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp.server"],
        env={
            "GARMIN_MCP_HOME": str(tmp_path / "home"),
            "SYSTEMROOT": "C:\\Windows",  # Windows needs it for sockets in the child
            "PATH": "",
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        started = time.monotonic()
        await session.initialize()
        return await job(session, time.monotonic() - started)


def test_the_handshake_is_quick_and_does_not_wait_for_garmin(tmp_path):
    async def job(session, handshake_seconds):
        tools = await session.list_tools()
        return handshake_seconds, {t.name for t in tools.tools}

    handshake, names = anyio.run(_session_run, tmp_path, job)
    assert handshake < 10  # no login happens during initialize
    assert "garmin_status" in names


def test_a_call_without_a_login_fails_cleanly_with_the_login_command(tmp_path):
    async def job(session, _):
        return await session.call_tool("garmin_status", {})

    result = anyio.run(_session_run, tmp_path, job)
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "garmin-mcp-auth login" in text
    assert result.structured_content is None  # the answer is sent once, as text
