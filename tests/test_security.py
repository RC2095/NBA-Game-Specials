"""
Tests for security.py (API key validation and the rate limiter's core
logic). Everything here is stdlib-only, so it runs without FastAPI, and
time is controlled with a fake clock so nothing sleeps or flakes.
"""

import threading

import pytest

import security
from security import SlidingWindowLimiter, api_key_is_valid, normalize_api_key


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _limiter(limit=3, window=60.0):
    clock = FakeClock()
    return SlidingWindowLimiter(limit, window, clock=clock), clock


# ============================================================
# normalize_api_key
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    ("\n", None),
    ("abc", "abc"),
    ("  abc  ", "abc"),
    ("abc\n", "abc"),
    ("a b", "a b"),  # interior whitespace is part of the key
])
def test_normalize_api_key(raw, expected):
    assert normalize_api_key(raw) == expected


# ============================================================
# api_key_is_valid
# ============================================================

def test_correct_key_is_valid():
    assert api_key_is_valid("secret-key-123", "secret-key-123") is True


def test_wrong_key_is_invalid():
    assert api_key_is_valid("wrong", "secret-key-123") is False


def test_missing_credential_is_invalid():
    assert api_key_is_valid(None, "secret-key-123") is False


def test_empty_credential_is_invalid():
    assert api_key_is_valid("", "secret-key-123") is False


def test_empty_credential_never_matches_an_empty_expected_key():
    """
    Regression test for a bug reproduced before it was fixed: with
    NBA_FOUL_API_KEY set to a blank string, a request carrying an empty
    X-API-Key header compared equal to the empty key and was let
    through, while looking like auth was enabled.
    """
    assert api_key_is_valid("", "") is False


def test_missing_expected_key_never_validates_anything():
    assert api_key_is_valid("anything", None) is False
    assert api_key_is_valid("anything", "") is False
    assert api_key_is_valid(None, None) is False
    assert api_key_is_valid("", None) is False


def test_prefix_of_the_key_is_invalid():
    assert api_key_is_valid("secret-key", "secret-key-123") is False


def test_key_with_extra_characters_is_invalid():
    assert api_key_is_valid("secret-key-1234", "secret-key-123") is False


def test_comparison_is_case_sensitive():
    assert api_key_is_valid("SECRET", "secret") is False


def test_non_ascii_keys_work():
    # hmac.compare_digest on str raises for non-ASCII; this must not.
    assert api_key_is_valid("clé-secrète-é", "clé-secrète-é") is True
    assert api_key_is_valid("clé-secrète-e", "clé-secrète-é") is False


def test_uses_constant_time_comparison():
    # Not a timing test (those are flaky) -- just pins the mechanism so a
    # refactor back to `==` is a conscious decision, not an accident.
    import inspect

    assert "compare_digest" in inspect.getsource(security.api_key_is_valid)


# ============================================================
# SlidingWindowLimiter: basic behavior
# ============================================================

def test_allows_up_to_the_limit():
    limiter, _ = _limiter(limit=3)
    assert [limiter.check("ip")[0] for _ in range(3)] == [True, True, True]


def test_blocks_the_request_after_the_limit():
    limiter, _ = _limiter(limit=3)
    results = [limiter.check("ip")[0] for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_allowed_requests_report_zero_retry_after():
    limiter, _ = _limiter(limit=2)
    assert limiter.check("ip") == (True, 0)


def test_keys_are_isolated_from_each_other():
    limiter, _ = _limiter(limit=2)
    limiter.check("a")
    limiter.check("a")
    assert limiter.check("a")[0] is False
    assert limiter.check("b")[0] is True


def test_full_capacity_returns_after_the_window_passes():
    limiter, clock = _limiter(limit=3, window=60.0)
    for _ in range(3):
        limiter.check("ip")
    assert limiter.check("ip")[0] is False

    clock.advance(61)

    assert [limiter.check("ip")[0] for _ in range(3)] == [True, True, True]


def test_window_is_sliding_not_fixed_buckets():
    # limit 2 per 60s; hits at t=0 and t=30.
    limiter, clock = _limiter(limit=2, window=60.0)
    start = clock.now
    limiter.check("ip")           # t=0
    clock.advance(30)
    limiter.check("ip")           # t=30
    clock.now = start + 59
    assert limiter.check("ip")[0] is False   # t=59: both still in window
    clock.now = start + 60
    assert limiter.check("ip")[0] is True    # t=60: the t=0 hit has expired
    assert limiter.check("ip")[0] is False   # but only one slot freed


def test_rejected_requests_are_not_counted():
    # A client hammering while limited must not extend its own lockout.
    limiter, clock = _limiter(limit=1, window=60.0)
    start = clock.now
    limiter.check("ip")
    for _ in range(20):
        clock.advance(1)
        assert limiter.check("ip")[0] is False
    clock.now = start + 60
    assert limiter.check("ip")[0] is True


# ============================================================
# SlidingWindowLimiter: Retry-After
# ============================================================

def test_retry_after_is_time_until_oldest_hit_expires():
    limiter, clock = _limiter(limit=1, window=60.0)
    limiter.check("ip")
    clock.advance(10)

    allowed, retry_after = limiter.check("ip")

    assert allowed is False
    assert retry_after == 50  # 60s window - 10s elapsed


def test_retry_after_is_accurate_following_it_succeeds():
    limiter, clock = _limiter(limit=1, window=60.0)
    limiter.check("ip")
    clock.advance(7)
    _, retry_after = limiter.check("ip")

    clock.advance(retry_after)

    assert limiter.check("ip")[0] is True


def test_retry_after_rounds_up_and_is_at_least_one_second():
    limiter, clock = _limiter(limit=1, window=60.0)
    limiter.check("ip")
    clock.advance(59.4)
    _, retry_after = limiter.check("ip")
    assert retry_after == 1  # 0.6s remaining rounds UP to 1, never 0


def test_retry_after_never_exceeds_the_window():
    limiter, _ = _limiter(limit=1, window=60.0)
    limiter.check("ip")
    _, retry_after = limiter.check("ip")
    assert 1 <= retry_after <= 60


# ============================================================
# SlidingWindowLimiter: memory (stale-key eviction)
# ============================================================

def test_stale_keys_are_evicted():
    """
    Regression test: the old limiter never removed an IP once it had
    been seen, so memory grew with every distinct client ever seen.
    """
    limiter, clock = _limiter(limit=5, window=60.0)
    for i in range(1000):
        limiter.check(f"ip-{i}")
    assert limiter.tracked_keys() == 1000

    clock.advance(61)
    limiter.check("fresh-client")  # triggers the sweep

    assert limiter.tracked_keys() == 1


def test_sweep_keeps_recently_active_keys():
    limiter, clock = _limiter(limit=5, window=60.0)
    limiter.check("old")
    clock.advance(50)
    limiter.check("recent")
    clock.advance(11)  # t=61: "old" is stale, "recent" is 11s old

    limiter.check("trigger")

    assert limiter.tracked_keys() == 2  # recent + trigger; old was swept


def test_client_regains_capacity_after_its_window_even_if_swept():
    limiter, clock = _limiter(limit=2, window=60.0)
    start = clock.now
    limiter.check("busy")
    limiter.check("busy")
    clock.now = start + 59
    limiter.check("other")          # some unrelated traffic; no sweep yet
    clock.now = start + 61          # sweep is due and "busy"'s hits have expired
    limiter.check("other")
    assert limiter.check("busy")[0] is True


def test_sweep_does_not_forget_a_client_still_inside_its_window():
    """
    The dangerous failure mode for eviction: sweeping a client that is
    still rate-limited would hand it a fresh allowance. A hit made 30s
    ago must survive a sweep and keep the client limited.
    """
    limiter, clock = _limiter(limit=1, window=60.0)
    start = clock.now
    clock.now = start + 40
    limiter.check("late")           # hit at t=40
    clock.now = start + 70          # sweep is due (70s since start); that hit is only 30s old
    limiter.check("trigger")
    assert limiter.check("late")[0] is False


# ============================================================
# SlidingWindowLimiter: construction and concurrency
# ============================================================

@pytest.mark.parametrize("bad_limit", [0, -1, -100])
def test_rejects_non_positive_limit(bad_limit):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(bad_limit)


@pytest.mark.parametrize("bad_window", [0, -1, -0.5])
def test_rejects_non_positive_window(bad_window):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(5, bad_window)


def test_default_clock_is_monotonic():
    # wall-clock adjustments (NTP, DST) must not let a client escape or
    # extend a limit.
    import inspect
    import time

    default = inspect.signature(SlidingWindowLimiter.__init__).parameters["clock"].default
    assert default is time.monotonic


def test_concurrent_requests_never_exceed_the_limit():
    clock = FakeClock()  # frozen: no hit can expire during the test
    limiter = SlidingWindowLimiter(100, 60.0, clock=clock)
    allowed_counts = []

    def worker():
        allowed = sum(1 for _ in range(50) if limiter.check("shared-ip")[0])
        allowed_counts.append(allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 8 threads x 50 attempts = 400 attempts against a limit of 100.
    assert sum(allowed_counts) == 100
