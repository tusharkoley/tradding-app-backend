from unittest.mock import patch

from corsheaders.middleware import CorsMiddleware
from django.db import DatabaseError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from stocks.api_security import StockApiOriginMiddleware, StockApiSecurityMiddleware, client_ip, consume_request


@override_settings(STOCK_API_ALLOWED_ORIGIN="https://tradezens.com",
                   CORS_ALLOWED_ORIGINS=["https://tradezens.com"],
                   STOCK_API_TRUST_RAILWAY_PROXY=False)
class StockSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.app = StockApiOriginMiddleware(CorsMiddleware(StockApiSecurityMiddleware(lambda r: HttpResponse("ok"))))

    def request(self, path="/stocks/prices/latest/", origin="https://tradezens.com", method="get"):
        headers = {"HTTP_ORIGIN": origin} if origin is not None else {}
        return getattr(self.factory, method)(path, **headers)

    @patch('stocks.api_security.consume_request', return_value=(True, 0))
    def test_all_stock_routes_share_ip_budget(self, consume):
        for path in ["/stocks/", "/stocks/companies/1/", "/stocks/company/AAPL.US/prices/",
                     "/stocks/technicals/latest/", "/stocks/industries/performance/"]:
            self.assertEqual(self.app(self.request(path)).status_code, 200)
        self.assertEqual(consume.call_count, 5)
        self.assertEqual(len({call.args[0] for call in consume.call_args_list}), 1)

    @patch('stocks.api_security.consume_request')
    def test_missing_and_unapproved_origins_are_denied_before_rate_limit(self, consume):
        for origin in [None, "null", "http://tradezens.com", "https://www.tradezens.com",
                       "https://tradezens.com.attacker.example", "https://tradezens.com/"]:
            self.assertEqual(self.app(self.request(origin=origin)).status_code, 403)
        consume.assert_not_called()

    @patch('stocks.api_security.consume_request', return_value=(False, 17))
    def test_429_is_readable_by_frontend_and_has_retry_after(self, consume):
        response = self.app(self.request())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '17')
        self.assertEqual(response['Access-Control-Allow-Origin'], 'https://tradezens.com')
        self.assertEqual(response['Cache-Control'], 'no-store')

    @patch('stocks.api_security.consume_request')
    def test_preflight_requires_approved_origin_but_does_not_consume_budget(self, consume):
        request = self.request(method="options")
        request.META['HTTP_ACCESS_CONTROL_REQUEST_METHOD'] = 'GET'
        self.assertEqual(self.app(request).status_code, 200)
        self.assertEqual(self.app(self.request(method="options", origin="https://evil.example")).status_code, 403)
        consume.assert_not_called()

    @patch('stocks.api_security.consume_request')
    def test_other_apis_are_unaffected(self, consume):
        self.assertEqual(self.app(self.request('/users/login/', origin=None)).status_code, 200)
        consume.assert_not_called()

    def test_untrusted_forwarding_headers_do_not_bypass_ip_budget(self):
        request = self.request()
        request.META.update(REMOTE_ADDR='192.0.2.1', HTTP_X_REAL_IP='192.0.2.2', HTTP_X_FORWARDED_FOR='192.0.2.3')
        self.assertEqual(client_ip(request), '192.0.2.1')
        with override_settings(STOCK_API_TRUST_RAILWAY_PROXY=True):
            self.assertEqual(client_ip(request), '192.0.2.2')

    @patch('stocks.api_security.logger')
    @patch('stocks.api_security.consume_request', side_effect=DatabaseError('unavailable'))
    def test_storage_failure_fails_closed(self, consume, logger):
        self.assertEqual(self.app(self.request()).status_code, 503)

    @patch('stocks.api_security.connection')
    @patch('stocks.api_security.transaction.atomic')
    @patch('stocks.api_security.StockApiRateWindow.objects')
    def test_twenty_first_call_is_blocked_then_rolling_window_expires(self, manager, atomic, connection):
        from types import SimpleNamespace
        from unittest.mock import Mock
        connection.features.has_select_for_update = True
        window = SimpleNamespace(requests=[], save=Mock())
        manager.select_for_update.return_value.get.return_value = window
        with patch('stocks.api_security.time.time', return_value=1000):
            for _ in range(20):
                self.assertEqual(consume_request('192.0.2.1'), (True, 0))
            self.assertEqual(consume_request('192.0.2.1'), (False, 60))
        with patch('stocks.api_security.time.time', return_value=1060):
            self.assertEqual(consume_request('192.0.2.1'), (True, 0))
        self.assertEqual(len(window.requests), 1)
        self.assertEqual(window.save.call_count, 21)
