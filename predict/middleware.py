"""Opt-in request timing middleware for production performance diagnosis."""
import logging
from time import perf_counter

from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

logger = logging.getLogger("predict.performance")


class RequestPerformanceMiddleware:
    """Log slow request duration and aggregate SQL cost without logging SQL text."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "PERF_LOGGING_ENABLED", False):
            return self.get_response(request)

        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            response = self.get_response(request)
        elapsed_ms = (perf_counter() - started) * 1000
        db_ms = sum(float(item.get("time") or 0) * 1000 for item in queries.captured_queries)
        threshold = getattr(settings, "PERF_LOG_MIN_MS", 100)

        if elapsed_ms >= threshold:
            logger.info(
                "REQUEST_PERF method=%s path=%s status=%s elapsed_ms=%.1f "
                "db_ms=%.1f query_count=%d",
                request.method,
                request.path[:240],
                response.status_code,
                elapsed_ms,
                db_ms,
                len(queries),
            )
        return response
