from datetime import date
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from stocks.models import Company, Price


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


class IndustryPerformanceRankingTests(TestCase):
    def setUp(self):
        cache.clear()
        self._create_company("AAA", "Technology")
        self._create_company("BBB", "Technology")
        self._create_company("CCC", "Finance", country="India")

        self._create_price("AAA", date(2024, 1, 1), 100)
        self._create_price("AAA", date(2024, 4, 1), 120)
        self._create_price("BBB", date(2024, 1, 1), 200)
        self._create_price("BBB", date(2024, 4, 1), 220)
        self._create_price("CCC", date(2024, 1, 1), 100)
        self._create_price("CCC", date(2024, 4, 1), 105)

    def _create_company(self, ticker, industry, country="United States"):
        return Company.objects.create(
            ticker=ticker,
            company_name=ticker,
            soctor=industry,
            industry=industry,
            description="Test company",
            country=country,
            address="Test address",
        )

    def _create_price(self, ticker, price_date, close):
        return Price.objects.create(
            ticker=ticker,
            date=price_date,
            open=close,
            close=close,
            high=close,
            low=close,
            volume=100,
            stock_splits=0,
            dividends=0,
        )

    def test_ranks_industries_using_latest_and_lookback_prices(self):
        response = self.client.get(
            reverse("industries-performance"),
            {"months": 3},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["as_of_date"], date(2024, 4, 1))
        self.assertEqual(
            response.data["rankings"],
            [
                {
                    "industry": "Technology",
                    "avg_return_pct": 15.0,
                    "company_count": 2,
                    "rank": 1,
                },
                {
                    "industry": "Finance",
                    "avg_return_pct": 5.0,
                    "company_count": 1,
                    "rank": 2,
                },
            ],
        )

    def test_country_filter_is_applied_before_price_lookups(self):
        response = self.client.get(
            reverse("industries-performance"),
            {"months": 3, "country": "India"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["rankings"]), 1)
        self.assertEqual(response.data["rankings"][0]["industry"], "Finance")

    def test_cached_response_only_checks_latest_price_date(self):
        url = reverse("industries-performance")
        self.client.get(url, {"months": 3})

        with self.assertNumQueries(1):
            response = self.client.get(url, {"months": 3})

        self.assertEqual(response.status_code, 200)
