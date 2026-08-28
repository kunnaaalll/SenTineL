"""Security, authentication, rate limiting, and observability middleware for Sentinel API.

Middlewares implemented:
- RequestIdMiddleware: Extracts or generates correlation IDs (X-Request-ID).
- SecurityHeadersMiddleware: Injects OWASP-recommended security headers.
- RequestSizeLimitMiddleware: Rejects requests exceeding size bounds (413).
- RateLimitMiddleware: Token bucket rate limiter per client IP/key (429 with Retry-After).
- AuthenticationMiddleware: Single-user staging API key / Bearer token validation (401).
- MetricsMiddleware: Records request latencies and response status codes.
"""

import logging
import secrets
import time
import uuid
from collections import defaultdict
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from api.errors import error_response
from config.settings import Settings, resolve_secret
from observability.logging import set_current_request_id
from observability.metrics import METRICS

logger = logging.getLogger(__name__)

# Paths exempt from authentication requirements
PUBLIC_PATHS = {"/health", "/ready", "/docs", "/redoc", "/openapi.json"}


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Propagates or generates X-Request-ID correlation tokens across logs and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        req_id = request.headers.get("X-Request-ID")
        if not req_id or not req_id.strip():
            req_id = uuid.uuid4().hex
        else:
            req_id = req_id.strip()

        set_current_request_id(req_id)
        request.state.request_id = req_id

        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            set_current_request_id(None)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attaches standard defensive security headers to every response."""

    def __init__(self, app: ASGIApp, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        if self.settings.security_headers_enabled:
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Guards against denial-of-service via oversized request payloads."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.max_bytes:
                    msg = (
                        f"Request payload ({length} bytes) exceeds maximum "
                        f"allowed size of {self.max_bytes} bytes."
                    )
                    return error_response(413, "payload_too_large", msg)
            except ValueError:
                pass
        return await call_next(request)


class RateLimiter:
    """In-memory sliding window rate limiter per client identifier."""

    def __init__(self, requests_per_minute: int, burst_limit: int):
        self.rate_per_sec = requests_per_minute / 60.0
        self.burst_limit = burst_limit
        self._tokens: dict[str, float] = defaultdict(lambda: float(burst_limit))
        self._last_check: dict[str, float] = defaultdict(time.time)

    def check_rate_limit(self, key: str) -> tuple[bool, float]:
        """Check if request is allowed. Returns (is_allowed, retry_after_seconds)."""
        now = time.time()
        last = self._last_check[key]
        elapsed = now - last
        self._last_check[key] = now

        # Replenish tokens
        self._tokens[key] = min(
            float(self.burst_limit), self._tokens[key] + elapsed * self.rate_per_sec
        )

        if self._tokens[key] >= 1.0:
            self._tokens[key] -= 1.0
            return True, 0.0
        else:
            missing = 1.0 - self._tokens[key]
            retry_after = round(missing / self.rate_per_sec, 2)
            return False, max(1.0, retry_after)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces rate limiting on non-exempt endpoints."""

    def __init__(self, app: ASGIApp, settings: Settings):
        super().__init__(app)
        self.settings = settings
        self.limiter = RateLimiter(
            requests_per_minute=settings.rate_limit_requests_per_minute,
            burst_limit=settings.rate_limit_burst_limit,
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.settings.rate_limit_enabled or request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Rate limit identifier: prioritize auth token / API key, then client IP
        client_ip = request.client.host if request.client else "unknown"
        auth_header = request.headers.get("authorization") or request.headers.get("x-api-key") or ""
        identifier = f"{client_ip}:{auth_header[:16]}" if auth_header else client_ip

        allowed, retry_after = self.limiter.check_rate_limit(identifier)
        if not allowed:
            res = error_response(
                429,
                "rate_limited",
                f"Rate limit exceeded. Please retry after {retry_after} seconds.",
            )
            res.headers["Retry-After"] = str(int(retry_after))
            return res

        return await call_next(request)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Validates API Key or Bearer token for protected endpoints."""

    def __init__(self, app: ASGIApp, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Check if auth is required for this request
        if not self.settings.auth_enabled or request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        expected_key = resolve_secret(self.settings.auth_api_key)
        if not expected_key:
            logger.error("Authentication is enabled but AUTH_API_KEY is not configured.")
            return error_response(
                401,
                "unauthorized",
                "Authentication is enabled but server credentials are not configured.",
            )

        # Check Authorization: Bearer <key> or X-API-Key: <key>
        provided_key: str | None = None
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            provided_key = auth_header[7:].strip()
        elif request.headers.get("x-api-key"):
            provided_key = request.headers.get("x-api-key", "").strip()

        if not provided_key:
            return error_response(
                401,
                "unauthorized",
                "Authentication required. Provide a valid Bearer token or X-API-Key header.",
            )

        # Constant-time comparison to prevent timing side-channel attacks
        if not secrets.compare_digest(provided_key, expected_key):
            return error_response(
                401,
                "unauthorized",
                "Invalid authentication credentials.",
            )

        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Tracks latency, requests, and error rates per endpoint."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            # Clean path to avoid high-cardinality route explosion
            path = request.url.path
            METRICS.record_http_request(request.method, path, status_code, duration_ms)
