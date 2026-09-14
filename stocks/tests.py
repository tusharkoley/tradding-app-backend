from unittest.mock import Mock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from stocks.models import Company


class UpdatePricesEodhdCommandTests(TestCase):
    def setUp(self):
        Company.objects.create(
            ticker="AAPL.US",
            company_name="Apple Inc",
            soctor="Technology",
            industry="Consumer Electronics",
            description="Test company",
            country="United States",
            address="1 Infinite Loop",
            website="https://example.com",
        )

    @patch("stocks.management.commands.update_prices_eodhd.requests.Session")
    def test_raises_when_eodhd_fetch_fails(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Ticker Not Found"
        mock_session.get.return_value = mock_response

        with self.assertRaises(CommandError):
            call_command(
                "update_prices_eodhd",
                api_token="test-token",
                default_exchange="US",
                tickers=["AAPL.US"],
                sleep=0,
            )
