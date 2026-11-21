from app.platform.rate_limit import MAX_TRACKED_CLIENTS, WINDOW_SECONDS, SlidingWindowLimiter, normalise


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
