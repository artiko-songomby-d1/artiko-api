from app.platform.rate_limit import MAX_TRACKED_CLIENTS, WINDOW_SECONDS, SlidingWindowLimiter


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


def test_idle_clients_are_forgotten_so_memory_stays_bounded():
    limiter = SlidingWindowLimiter()
    for i in range(MAX_TRACKED_CLIENTS):
        limiter.check(f"c{i}", 5, now=0)
    limiter.check("new", 5, now=WINDOW_SECONDS + 10)
    assert len(limiter.hits) == 1
