"""
Tests for the middleware built in main.py.

Rate limiting is tested against a tiny standalone Starlette app that
wraps RateLimitMiddleware directly, with its OWN low limit and its own
fresh counter state -- NOT against the shared main.app, because (see
conftest.py) that app's rate limit is intentionally set very high for
the rest of the test suite and is baked in at import time, so it can't
be lowered per-test the way API_KEY can be monkeypatched. This gives
genuine coverage of the 429-triggering logic without needing to touch
the main app's shared, already-high-limit state.

CORS is tested against the real main.app instead, since its default
(empty allow-list) behavior doesn't need to change per-test -- just
confirming the default-deny posture holds.
"""

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient as StarletteTestClient

from main import RateLimitMiddleware, app as main_app


async def _ping(request):
    return PlainTextResponse("ok")


def _build_rate_limited_app(limit: int):
    test_app = Starlette(routes=[Route("/ping", _ping)])
    test_app.add_middleware(RateLimitMiddleware, requests_per_minute=limit)
    return test_app


def test_rate_limit_allows_requests_under_the_limit():
    client = StarletteTestClient(_build_rate_limited_app(limit=3))
    statuses = [client.get("/ping").status_code for _ in range(3)]
    assert statuses == [200, 200, 200]


def test_rate_limit_blocks_requests_over_the_limit():
    client = StarletteTestClient(_build_rate_limited_app(limit=3))
    statuses = [client.get("/ping").status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]


def test_rate_limit_429_response_has_useful_body():
    client = StarletteTestClient(_build_rate_limited_app(limit=1))
    client.get("/ping")
    response = client.get("/ping")
    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"].lower()


def test_rate_limit_is_per_middleware_instance_not_global():
    """
    Each _build_rate_limited_app call creates a fresh RateLimitMiddleware
    instance with its own counter -- this confirms two separately-built
    apps don't share state, which is what makes testing this in
    isolation from main.app's shared instance valid in the first place.
    """
    client_a = StarletteTestClient(_build_rate_limited_app(limit=2))
    client_b = StarletteTestClient(_build_rate_limited_app(limit=2))

    # Exhaust client_a's limit
    client_a.get("/ping")
    client_a.get("/ping")
    blocked = client_a.get("/ping")
    assert blocked.status_code == 429

    # client_b's independent app/middleware is unaffected
    fresh = client_b.get("/ping")
    assert fresh.status_code == 200


# ============================================================
# CORS (tested against the real app -- default behavior only)
# ============================================================

main_client = StarletteTestClient(main_app)


def test_cors_blocks_unlisted_origin_by_default():
    """
    NBA_FOUL_ALLOWED_ORIGINS is unset in this test environment, so the
    default is an empty allow-list -- confirms no CORS header is added
    for a cross-origin request, which is what causes a browser to block
    the response client-side. The request itself still succeeds
    server-side (CORS is enforced by browsers, not servers); this test
    checks the header that makes that enforcement happen is absent.
    """
    response = main_client.get(
        "/health", headers={"Origin": "https://not-on-the-allowlist.example.com"}
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in {
        key.lower() for key in response.headers.keys()
    }


def test_cors_same_origin_request_unaffected():
    # A request with no Origin header at all (not a cross-origin browser
    # request) should work normally regardless of CORS config.
    response = main_client.get("/health")
    assert response.status_code == 200


# ============================================================
# Retry-After and the /health exemption (isolated apps, like the
# rate-limit tests above)
# ============================================================

async def _health(request):
    return PlainTextResponse("healthy")


def _build_app_with_health(limit: int):
    test_app = Starlette(routes=[Route("/ping", _ping), Route("/health", _health)])
    test_app.add_middleware(RateLimitMiddleware, requests_per_minute=limit)
    return test_app


def test_rate_limited_response_includes_retry_after_header():
    client = StarletteTestClient(_build_rate_limited_app(limit=1))
    client.get("/ping")

    response = client.get("/ping")

    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60


def test_health_endpoint_is_never_rate_limited():
    """
    If /health could answer 429 during a traffic burst, a hosting
    platform would see failing health checks and restart the app --
    turning a load spike into a self-inflicted outage.
    """
    client = StarletteTestClient(_build_app_with_health(limit=1))
    client.get("/ping")
    assert client.get("/ping").status_code == 429  # genuinely limited now

    statuses = [client.get("/health").status_code for _ in range(10)]

    assert statuses == [200] * 10


def test_health_requests_do_not_consume_the_rate_limit_budget():
    client = StarletteTestClient(_build_app_with_health(limit=2))
    for _ in range(20):
        client.get("/health")

    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429
