import pytest
import requests

from bnm.http import CachedFetcher


class FakeResp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses):
        self.responses, self.headers, self.calls = list(responses), {}, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(headers or {})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(tmp_path, responses):
    f = CachedFetcher(tmp_path, "test", delay=0)
    f.session = FakeSession(responses)
    return f


def test_conditional_get_and_cache_fallback(tmp_path):
    f = make(tmp_path, [
        FakeResp(200, b'{"ok":1}', {"ETag": '"abc"'}),
        FakeResp(304),
        requests.ConnectionError("down"),
        FakeResp(200, b"<html>maintenance</html>"),
    ])
    url = "https://example.test/a.geojson"

    def validate(b):
        if not b.startswith(b"{"):
            raise ValueError("not json")

    assert f.get(url, validate).content == b'{"ok":1}'
    r = f.get(url, validate)
    assert r.content == b'{"ok":1}' and not r.stale
    assert f.session.calls[1]["If-None-Match"] == '"abc"'
    r = f.get(url, validate)
    assert r.stale and "down" in r.error and r.content == b'{"ok":1}'
    r = f.get(url, validate)  # garbage response rejected, cache kept
    assert r.stale and r.content == b'{"ok":1}'


def test_404_is_missing_and_clears_cache(tmp_path):
    f = make(tmp_path, [FakeResp(200, b"x"), FakeResp(404)])
    f.get("https://example.test/b")
    r = f.get("https://example.test/b")
    assert r.missing and r.content == b""


def test_failure_without_cache_raises(tmp_path):
    f = make(tmp_path, [requests.ConnectionError("down")])
    with pytest.raises(requests.ConnectionError):
        f.get("https://example.test/c")
