"""
Sliding Window IP-Based Rate Limiting Middleware
File: backend/api/middleware/rate_limiter.py

Enforces Section 2.3 of docs/BE_rulebook.md:
- /api/boards: 60 requests / IP / minute
- /api/stock/:symbol/chart: 120 requests / IP / minute
- /api/health: 30 requests / IP / minute
- Returns HTTP 429 with Retry-After header upon violation.
"""

from __future__ import annotations
import time
from collections import defaultdict
from typing import Dict, List, Tuple
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class SlidingWindowRateLimiter(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # client_ip:endpoint_category -> list of float timestamps
        self.history: Dict[str, List[float]] = defaultdict(list)
        # Limit definitions: (max_requests, window_seconds)
        self.limits: Dict[str, Tuple[int, int]] = {
            "/api/boards": (60, 60),
            "/api/health": (30, 60),
            "/api/stock": (120, 60),
            "/api/execution": (60, 60),
        }
        self.default_limit: Tuple[int, int] = (120, 60)

    def _get_category(self, path: str) -> str:
        for prefix in self.limits:
            if path.startswith(prefix):
                return prefix
        return "default"

    async def dispatch(self, request: Request, call_next):
        headers = request.headers
        client_ip = (
            headers.get("cf-connecting-ip")
            or headers.get("x-real-ip")
            or (headers.get("x-forwarded-for").split(",")[0].strip() if headers.get("x-forwarded-for") else None)
            or (request.client.host if request.client else "127.0.0.1")
        )
        category = self._get_category(request.url.path)
        max_reqs, window_sec = self.limits.get(category, self.default_limit)

        now = time.time()
        key = f"{client_ip}:{category}"
        window_start = now - window_sec

        # Clean up timestamps older than window
        timestamps = [t for t in self.history[key] if t > window_start]
        self.history[key] = timestamps

        if len(timestamps) >= max_reqs:
            oldest_in_window = timestamps[0]
            retry_after = max(1, int(window_sec - (now - oldest_in_window)))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
                content={
                    "success": False,
                    "error": "Rate limit exceeded for endpoint. Please slow down.",
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "retry_after_seconds": retry_after,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        self.history[key].append(now)
        response = await call_next(request)
        return response
