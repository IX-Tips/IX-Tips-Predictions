from unittest import TestCase
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from predict.middleware import RequestPerformanceMiddleware


class RequestPerformanceMiddlewareTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/top-picks/")

    @override_settings(PERF_LOGGING_ENABLED=False)
    def test_disabled_middleware_passes_through_without_logging(self):
        response = HttpResponse("ok")
        middleware = RequestPerformanceMiddleware(lambda request: response)

        with patch("predict.middleware.logger.info") as log_info:
            self.assertIs(middleware(self.request), response)

        log_info.assert_not_called()

    @override_settings(PERF_LOGGING_ENABLED=True, PERF_LOG_MIN_MS=0)
    def test_enabled_middleware_logs_aggregate_request_metrics(self):
        middleware = RequestPerformanceMiddleware(lambda request: HttpResponse("ok", status=200))

        with patch("predict.middleware.logger.info") as log_info:
            response = middleware(self.request)

        self.assertEqual(response.status_code, 200)
        log_info.assert_called_once()
        message = log_info.call_args.args[0]
        self.assertIn("REQUEST_PERF", message)
        self.assertIn("query_count=%d", message)
        self.assertNotIn("SELECT", message)
