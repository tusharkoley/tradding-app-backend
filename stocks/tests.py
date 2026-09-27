from datetime import date, timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from stocks.models import Company, Price, TechnicalIndicators


class RelativeStrengthTests(TestCase):
    def setUp(self):
        cache.clear()

    def add_prices(self, ticker, dates, closes):
        Company.objects.create(ticker=ticker, company_name=ticker, industry='Software')
        Price.objects.bulk_create([
            Price(ticker=ticker, date=dt, open=close, high=close, low=close,
                  close=close, volume=100, stock_splits=0, dividends=0)
            for dt, close in zip(dates, closes)
        ])

    def compute(self, **kwargs):
        call_command('compute_technicals', days=0, stdout=StringIO(), **kwargs)

    def test_filtered_run_keeps_full_peer_ranking_and_own_trading_calendar(self):
        dates = [date(2024, 1, 1) + timedelta(days=i * 2) for i in range(64)]
        self.add_prices('AAA', dates, [100] * 63 + [120])
        self.add_prices('BBB', dates, [100] * 63 + [150])
        # Another exchange trades between these dates. It must not shorten
        # AAA's 63-observation lookback or manufacture prices on its off days.
        other_dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(127)]
        self.add_prices('CCC', other_dates, [100] * 127)
        self.compute(tickers=['AAA'])
        self.assertEqual(set(TechnicalIndicators.objects.values_list('ticker', flat=True)), {'AAA'})
        latest = TechnicalIndicators.objects.get(ticker='AAA', date=dates[-1])
        self.assertAlmostEqual(latest.rs_industry, 200 / 3)
        self.assertIsNone(TechnicalIndicators.objects.get(ticker='AAA', date=dates[-2]).rs_industry)
        self.compute()
        latest.refresh_from_db()
        self.assertAlmostEqual(latest.rs_industry, 200 / 3)

    def test_insufficient_history_and_invalid_closes_remain_unavailable(self):
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(64)]
        self.add_prices('SHORT', dates[:63], [100] * 63)
        self.add_prices('ZERO', dates, [0] + [100] * 63)
        self.add_prices('VALID', dates, [100] * 64)
        self.compute()
        self.assertFalse(TechnicalIndicators.objects.filter(ticker__in=['SHORT', 'ZERO'], rs_industry__isnull=False).exists())
        self.assertEqual(TechnicalIndicators.objects.get(ticker='VALID', date=dates[-1]).rs_industry, 100)

    @patch('stocks.management.commands.compute_technicals.TechnicalIndicators.objects.bulk_create', side_effect=RuntimeError('DB failure'))
    def test_failed_upsert_reports_failure(self, mocked_create):
        self.add_prices('AAA', [date(2024, 1, 1)], [100])
        with self.assertRaises(CommandError):
            self.compute()

    def test_latest_api_uses_newest_row_and_filters_after_selection(self):
        for ticker, old_rs, new_rs in [('AAA', None, 95), ('BBB', 99, 40), ('CCC', 98, None)]:
            TechnicalIndicators.objects.create(ticker=ticker, date=date(2024, 1, 1), rs_industry=old_rs)
            TechnicalIndicators.objects.create(ticker=ticker, date=date(2024, 4, 1), rs_industry=new_rs)
        response = self.client.get(reverse('technicals-latest'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['ticker']: row['rs_industry'] for row in response.data}, {'AAA': 95, 'BBB': 40, 'CCC': None})
        for row in response.data:
            self.assertEqual(row['date'], '2024-04-01')
        response = self.client.get(reverse('technicals-latest'), {'rs_min': 90})
        self.assertEqual([row['ticker'] for row in response.data], ['AAA'])

    def test_latest_prices_use_newest_date(self):
        self.add_prices('AAA', [date(2024, 1, 1), date(2024, 4, 1)], [100, 120])
        response = self.client.get(reverse('prices-latest'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['close'], 120)

    def test_postgres_latest_query_retains_date_ordering_inside_subquery(self):
        from django.db.backends.postgresql.base import DatabaseWrapper
        from rest_framework.test import APIRequestFactory
        from stocks.views import TechnicalIndicatorsLatestList

        postgres = DatabaseWrapper({'NAME': 'unused'}, alias='sql_only')
        captured = []

        def capture_query(queryset, **kwargs):
            captured.append(queryset.query.get_compiler(connection=postgres).as_sql()[0])
            return Mock(data=[])

        request = APIRequestFactory().get('/stocks/technicals/latest/', {'rs_min': 90})
        with patch('stocks.views.connection.vendor', 'postgresql'), patch('stocks.views.TechnicalIndicatorsSerializer', side_effect=capture_query):
            response = TechnicalIndicatorsLatestList.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn('DISTINCT ON', captured[0])
        self.assertRegex(captured[0], r'ORDER BY U\d+\."ticker" ASC, U\d+\."date" DESC')


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
