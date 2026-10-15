"""
Global Exception Handling & Error Masking Middleware
File: backend/api/middleware/error_handler.py

Enforces Section 2.4 of docs/BE_rulebook.md:
- Catches unhandled internal exceptions.
- Masks raw database queries, credentials, and Python stack traces.
- Standardizes all client error responses into a clean envelope.
"""

from __future__ import annotations
import logging
import traceback
from datetime import datetime, timezone
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("api_error_handler")


class SafeErrorHandlingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # Log full stack trace internally for engineering diagnostics
            logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}\n{traceback.format_exc()}")

            now_iso = datetime.now(timezone.utc).isoformat()
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": "An internal service error occurred. Please retry shortly.",
                    "error_code": "INTERNAL_SERVER_ERROR",
                    "timestamp": now_iso,
                },
            )
