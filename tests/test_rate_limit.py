import pytest
from starlette.requests import Request

from app.platform.config import get_settings
from app.platform.rate_limit import (
    MAX_TRACKED_CLIENTS,
    WINDOW_SECONDS,
    SlidingWindowLimiter,
    client_key,
    normalise,
)


def test_blocks_after_the_limit_and_reports_the_wait():
    limiter = SlidingWindowLimiter()
    assert [limiter.check("a", 2, now=t) for t in (0, 1)] == [None, None]
    assert limiter.check("a", 2, now=2) == WINDOW_SECONDS - 2 + 1


def test_clients_are_counted_apart():
    limiter = SlidingWindowLimiter()
    assert limiter.check("a", 1, now=0) is None
    assert limiter.check("b", 1, now=0) is None
    assert limiter.check("a", 1, now=1) is not None


def test_window_slides():
    limiter = SlidingWindowLimiter()
    limiter.check("a", 1, now=0)
    assert limiter.check("a", 1, now=WINDOW_SECONDS + 1) is None


def test_memory_stays_bounded_even_when_every_client_is_recent():
    limiter = SlidingWindowLimiter()
    for i in range(MAX_TRACKED_CLIENTS + 500):
        limiter.check(f"c{i}", 5, now=0)
    assert len(limiter.hits) == MAX_TRACKED_CLIENTS
    assert "c0" not in limiter.hits and f"c{MAX_TRACKED_CLIENTS + 499}" in limiter.hits


def test_ipv6_addresses_of_one_network_are_one_client():
    assert normalise("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == normalise("2001:db8:1:2::1")
    assert normalise("2001:db8:1:2::1") != normalise("2001:db8:1:3::1")
    assert normalise("203.0.113.7") == "203.0.113.7"
    assert normalise("not-an-ip") == "not-an-ip"


def test_an_ipv4_client_written_as_ipv6_keeps_its_own_counter():
    assert normalise("::ffff:203.0.113.7") == "203.0.113.7"
    assert normalise("::ffff:203.0.113.7") != normalise("::ffff:203.0.113.8")


PROXY = "10.0.0.5"


def request_through_proxy(**headers: str) -> Request:
    """A request as the app sees it behind a proxy: the socket peer is the proxy, the visitor is in a header."""
    raw = [(name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "client": (PROXY, 4321), "headers": raw})


@pytest.fixture
def behind_cloudflare(monkeypatch):
    monkeypatch.setattr(get_settings(), "client_ip_header", "CF-Connecting-IP")


def test_without_a_configured_header_every_visitor_behind_the_proxy_is_one_client():
    assert client_key(request_through_proxy(cf_connecting_ip="203.0.113.7")) == PROXY
    assert client_key(request_through_proxy(cf_connecting_ip="198.51.100.9")) == PROXY


def test_the_configured_header_tells_visitors_apart(behind_cloudflare):
    assert client_key(request_through_proxy(cf_connecting_ip="203.0.113.7")) == "203.0.113.7"
    assert client_key(request_through_proxy(cf_connecting_ip="198.51.100.9")) == "198.51.100.9"
    assert client_key(request_through_proxy(cf_connecting_ip="2001:db8:1:2:aaaa::1")) == "2001:db8:1:2::"


def test_another_header_a_client_sends_does_not_change_its_key(behind_cloudflare):
    forged = request_through_proxy(cf_connecting_ip="203.0.113.7", x_forwarded_for="1.2.3.4", x_real_ip="1.2.3.4")
    assert client_key(forged) == "203.0.113.7"


def test_a_header_that_is_missing_or_not_an_address_falls_back_to_the_shared_counter(behind_cloudflare):
    assert client_key(request_through_proxy()) == PROXY
    for value in ("203.0.113.7:5678", "[2001:db8::1]:443", "*", "unknown", "not-an-ip", "203.0.113.7,", " "):
        assert client_key(request_through_proxy(cf_connecting_ip=value)) == PROXY, value


def test_in_a_list_only_the_entry_written_by_the_nearest_proxy_counts(monkeypatch):
    monkeypatch.setattr(get_settings(), "client_ip_header", "x-client-chain")
    assert client_key(request_through_proxy(x_client_chain="1.2.3.4, 203.0.113.7")) == "203.0.113.7"
