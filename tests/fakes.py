"""A stand-in for garminconnect.Garmin, so tool logic is tested without the network.

Any `get_*`/`set_*`/... method call is recorded and answered from `responses`: a value
is returned as-is, an exception is raised, a callable is called with the arguments.
Payload shapes follow what Garmin returns; every value in them is synthetic.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from garmin_mcp import config
from garmin_mcp.client import Cache, Ctx


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, content: bytes = b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """Records requests; answers with a queue of status codes."""

    def __init__(self, *statuses: int):
        self.statuses = list(statuses) or [200]
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return FakeResponse(status, {"ok": status < 400})


class FakeClient:
    def __init__(self, owner: FakeGarmin):
        self._owner = owner
        self._api_session = FakeHttp()

    def connectapi(self, path: str, **kwargs: Any) -> Any:
        return self._owner._answer("connectapi", (path,), kwargs, key=path)

    def request(self, method: str, _domain: str, path: str, **kwargs: Any) -> Any:
        return self._owner._answer(f"{method.lower()}", (path,), kwargs, key=f"{method} {path}")

    def put(self, _domain: str, path: str, **kwargs: Any) -> Any:
        return self._owner._answer("put", (path,), kwargs, key=f"PUT {path}")

    def post(self, _domain: str, path: str, **kwargs: Any) -> Any:
        return self._owner._answer("post", (path,), kwargs, key=f"POST {path}")

    def delete(self, _domain: str, path: str, **kwargs: Any) -> Any:
        return self._owner._answer("delete", (path,), kwargs, key=f"DELETE {path}")

    def download(self, path: str, **kwargs: Any) -> Any:
        return self._owner._answer("download", (path,), kwargs, key=path)


class FakeGarmin:
    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        full_name: str = "Test Runner",
        display_name: str = "test-runner",
        unit_system: str = "metric",
    ):
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, tuple, dict]] = []
        self.full_name = full_name
        self.display_name = display_name
        self.unit_system = unit_system
        self.client = FakeClient(self)

    def _answer(self, name: str, args: tuple, kwargs: dict, key: str | None = None) -> Any:
        self.calls.append((name, args, kwargs))
        value = self.responses.get(key or name, self.responses.get(name))
        if isinstance(value, dict) and key is not None and key in value and name != key:
            value = value[key]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(*args, **kwargs)
        return value

    def get_full_name(self) -> str:
        return self.full_name

    def called(self, name: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args: Any, **kwargs: Any) -> Any:
            return self._answer(name, args, kwargs)

        return method


def make_cfg(tmp_path, **env: str) -> config.Config:
    # A fixed zone (with summer time) so the tests mean the same on every machine.
    base = {"GARMIN_MCP_HOME": str(tmp_path / "home"), "GARMIN_MCP_TIMEZONE": "Europe/Stockholm"}
    base.update(env)
    return config.load(base)


class FixedCtx(Ctx):
    """A Ctx whose 'today' is pinned, so tests don't depend on the calendar."""

    def __init__(self, g: Any, cfg: config.Config, today: date = date(2026, 10, 7)):
        super().__init__(g, cfg, ZoneInfo(cfg.timezone), Cache())
        self._today = today

    def today(self) -> date:
        return self._today

    def day(self, value, *, default="today", name="date"):
        from garmin_mcp import dates

        text = (value or default).strip().lower()
        if text == "today":
            return self._today
        if text == "yesterday":
            return date.fromordinal(self._today.toordinal() - 1)
        return dates.parse(text, self.tz, name=name)

    def span(self, start, end, *, max_days, default_days=7):
        from datetime import timedelta

        from garmin_mcp.errors import InputError

        last = self.day(end, name="end_date")
        first = self.day(start, name="start_date") if start else last - timedelta(days=default_days - 1)
        if first > last:
            raise InputError(f"start_date {first} is after end_date {last}")
        if (last - first).days + 1 > max_days:
            raise InputError(f"That range is too long; this view allows at most {max_days}.")
        return first, last
