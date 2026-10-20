"""
Request-guard primitives: API-key checking and rate limiting.

Deliberately stdlib-only (no FastAPI/Starlette imports) so this logic can
be unit-tested in complete isolation. main.py wires these into the
actual request path; the decisions themselves live here.

Why this module exists, in terms of bugs it fixes:

  * An API key configured as an EMPTY STRING (what you get from copying
    `.env.example`'s blank `NBA_FOUL_API_KEY=` line into a hosting
    platform's environment settings) used to leave auth switched on but
    trivially bypassable: `os.environ.get()` returns "" (not None), so
    the "is auth disabled?" check said no, and then a request carrying an
    empty `X-API-Key:` header compared equal to the empty key and was
    let through. Reproduced before the fix. A blank key is now treated
    as unset, and an empty/missing credential can never validate.

  * The rate limiter kept one list of timestamps per client IP forever.
    IPs that sent a few requests and never returned were never evicted,
    so memory grew with the number of distinct clients ever seen.
    Stale entries are now swept out.

  * Rate-limited responses had no `Retry-After` header, so well-behaved
    clients had no way to know how long to back off.
"""

import hmac
import math
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional, Tuple

# Not enforced -- main.py logs a warning below this length. Short keys
# are guessable, and unlike a password there's no lockout to slow
# guessing down beyond the rate limiter.
MIN_RECOMMENDED_API_KEY_LENGTH = 16


# ============================================================
# API KEY
# ============================================================

def normalize_api_key(raw: Optional[str]) -> Optional[str]:
    """
    Turns the raw environment value into the configured key, or None if
    auth should be considered disabled. Blank and whitespace-only values
    count as unset: many hosting platforms can only express "no value"
    as an empty string, and (see module docstring) treating a blank key
    as a real key is what made the empty-header bypass possible.
    Surrounding whitespace is stripped, since secret managers commonly
    add a trailing newline.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


def api_key_is_valid(provided: Optional[str], expected: Optional[str]) -> bool:
    """
    True only if `provided` matches `expected`.

    Fails closed: if either side is missing or empty, the answer is
    False -- an empty credential can never validate, even against an
    (incorrectly) empty expected key. Uses a constant-time comparison so
    response timing doesn't leak how many leading characters of a guess
    were correct.

    Callers decide separately whether auth is enabled at all (see
    normalize_api_key); this function only answers "does this credential
    match", never "is a credential required".
    """
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


# ============================================================
# RATE LIMITING
# ============================================================

class SlidingWindowLimiter:
    """
    Allows at most `limit` hits per `window_seconds` for each key
    (typically a client IP), counted over a true sliding window rather
    than fixed buckets, so a burst straddling a bucket boundary can't
    get double the limit.

    In-memory and per-process: state resets on restart and is not shared
    between multiple app instances. Requests that are rejected are NOT
    counted, so a client that keeps hammering while limited doesn't
    extend its own lockout.

    `clock` is injectable so tests can control time deterministically.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock()

    def check(self, key: str) -> Tuple[bool, int]:
        """
        Records a hit for `key` if allowed.

        Returns (allowed, retry_after_seconds). retry_after_seconds is 0
        when allowed; otherwise the whole number of seconds (>= 1) until
        the oldest counted hit leaves the window and a slot frees up.
        """
        now = self._clock()

        with self._lock:
            self._sweep_if_due(now)

            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits

            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = math.ceil(hits[0] + self.window_seconds - now)
                return False, max(1, retry_after)

            hits.append(now)
            return True, 0

    def tracked_keys(self) -> int:
        """Number of keys currently held in memory (for tests/monitoring)."""
        with self._lock:
            return len(self._hits)

    def _sweep_if_due(self, now: float) -> None:
        """
        Drops keys whose most recent hit has already left the window.
        Runs at most once per window, so the cost is amortized and a
        flood of one-off clients can't grow memory without bound.
        Caller must hold the lock.
        """
        if now - self._last_sweep < self.window_seconds:
            return

        cutoff = now - self.window_seconds
        stale = [
            key for key, hits in self._hits.items()
            if not hits or hits[-1] <= cutoff
        ]
        for key in stale:
            del self._hits[key]

        self._last_sweep = now
