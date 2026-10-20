"""
Pytest configuration applied before any test module is collected or
imported.

This sets NBA_FOUL_RATE_LIMIT_PER_MINUTE to a very high value BEFORE
main.py is ever imported by any test file. This matters for a subtle
but real reason: main.py's rate-limit middleware reads this
environment variable once, at import time (`app.add_middleware(
RateLimitMiddleware, requests_per_minute=RATE_LIMIT_REQUESTS_PER_MINUTE)`),
and bakes it into the middleware instance as a fixed attribute -- it is
NOT re-read per request, and unlike NBA_FOUL_API_KEY it can't be
monkeypatched per-test either, since the limiter's internal counter
state is a single dict living on that one middleware instance attached
to the single `app` object every test file imports and shares.

Every test across every test file in this suite shares that same `app`
and the same simulated client IP (Starlette's TestClient uses a fixed
host for all requests). With the default 60 requests/minute limit, a
test suite this size easily exceeds that within a single pytest run --
without this fix, tests would start failing with 429 Too Many Requests
errors that have nothing to do with whatever is actually being tested,
purely as a side effect of test execution order and how many tests ran
before them. This conftest.py is pytest's standard mechanism for
running setup before test collection in this directory, which is
exactly early enough to beat main.py's import-time middleware setup.
"""

import os

os.environ.setdefault("NBA_FOUL_RATE_LIMIT_PER_MINUTE", "1000000")
