"""The Windows 403 fallback (python-garminconnect #444)."""

from garmin_mcp.transport import HybridSession, install

from .fakes import FakeGarmin, FakeHttp


def hybrid(mode="auto", inner=(200,), cffi=(200,)):
    h = HybridSession(FakeHttp(*inner), mode)
    h._cffi = FakeHttp(*cffi)
    return h


class TestAuto:
    def test_ordinary_calls_stay_on_requests(self):
        h = hybrid()
        assert h.request("GET", "u").status_code == 200
        assert len(h._inner.calls) == 1 and h._cffi.calls == []

    def test_a_403_is_retried_once_through_curl_cffi(self):
        h = hybrid(inner=(403,), cffi=(200,))
        assert h.request("GET", "u", params={"a": 1}).status_code == 200
        assert h._cffi.calls == [("GET", "u", {"params": {"a": 1}})]

    def test_after_curl_cffi_gets_through_it_is_used_from_then_on(self):
        h = hybrid(inner=(403, 200), cffi=(200,))
        h.request("GET", "first")
        h.request("GET", "second")
        assert [c[1] for c in h._cffi.calls] == ["first", "second"]
        assert h.switched_at is not None and "switched" in h.describe()

    def test_a_403_that_curl_cffi_also_gets_does_not_switch(self):
        h = hybrid(inner=(403,), cffi=(403,))
        assert h.request("GET", "u").status_code == 403
        assert h.switched_at is None

    def test_uploads_never_go_through_curl_cffi(self):
        h = hybrid(inner=(403,), cffi=(200,))
        assert h.request("POST", "u", files={"f": b"x"}).status_code == 403
        assert h._cffi.calls == []


class TestModes:
    def test_requests_mode_never_retries(self):
        h = hybrid("requests", inner=(403,))
        assert h.request("GET", "u").status_code == 403
        assert h._cffi.calls == []

    def test_curl_cffi_mode_sends_everything_but_uploads_through_curl_cffi(self):
        h = hybrid("curl_cffi")
        h.request("GET", "a")
        h.request("POST", "b", files={})
        assert [c[1] for c in h._cffi.calls] == ["a"]
        assert [c[1] for c in h._inner.calls] == ["b"]


class TestInstall:
    def test_it_replaces_the_api_session(self):
        g = FakeGarmin()
        h = install(g, "auto")
        assert g.client._api_session is h

    def test_it_switches_itself_off_when_the_attribute_is_gone(self):
        g = FakeGarmin()
        del g.client._api_session
        assert install(g, "auto") is None
