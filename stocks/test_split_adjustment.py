import os
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from stocks.models import Price, StockSplit, SplitAdjustedPrice
from stocks.management.commands.backfill_split_adjusted_prices import parse_splits


class SplitAdjustmentTests(TestCase):
    def setUp(self):
        for day in ['2014-06-06', '2014-06-09', '2020-08-28', '2020-08-31']:
            Price.objects.create(ticker='AAPL', date=day, open=280, high=308, low=252,
                                 close=280, volume=100, dividends=0, stock_splits=0)
        StockSplit.objects.create(ticker='AAPL', date='2014-06-09', ratio=7)
        StockSplit.objects.create(ticker='AAPL', date='2020-08-31', ratio=4)

    def run_local(self, **options):
        call_command('backfill_split_adjusted_prices', tickers=['AAPL'], local_splits=True,
                     stdout=StringIO(), **options)

    def test_compound_splits_boundary_and_idempotency(self):
        self.run_local()
        rows = list(SplitAdjustedPrice.objects.order_by('price__date'))
        self.assertEqual([r.close for r in rows], [10, 70, 70, 280])
        self.assertEqual(rows[0].high, 11)
        self.assertEqual(rows[0].low, 9)
        self.assertEqual(rows[0].open, 10)
        ids = [r.pk for r in rows]
        self.run_local()
        self.assertEqual(list(SplitAdjustedPrice.objects.order_by('price__date').values_list('pk', flat=True)), ids)
        self.assertEqual(set(Price.objects.values_list('close', flat=True)), {280})

    def test_corrections_future_splits_and_new_prices(self):
        self.run_local()
        StockSplit.objects.filter(ticker='AAPL', date='2020-08-31').update(ratio=2)
        StockSplit.objects.create(ticker='AAPL', date='2099-01-01', ratio=100)
        Price.objects.create(ticker='AAPL', date='2021-01-04', open=300, high=300, low=300,
                             close=300, volume=100, dividends=0, stock_splits=0)
        self.run_local()
        self.assertEqual(SplitAdjustedPrice.objects.get(price__date='2014-06-06').close, 20)
        self.assertEqual(SplitAdjustedPrice.objects.get(price__date='2021-01-04').close, 300)

    def test_dry_run_and_reverse_split(self):
        self.run_local(dry_run=True)
        self.assertFalse(SplitAdjustedPrice.objects.exists())
        StockSplit.objects.all().delete()
        StockSplit.objects.create(ticker='AAPL', date='2020-08-31', ratio='0.1')
        self.run_local()
        self.assertEqual(SplitAdjustedPrice.objects.get(price__date='2020-08-28').close, 2800)

    def test_invalid_price_rolls_back_rebuild(self):
        self.run_local()
        Price.objects.filter(date='2014-06-06').update(close=float('inf'))
        from django.core.management import CommandError
        with self.assertRaises(CommandError):
            self.run_local()
        self.assertEqual(SplitAdjustedPrice.objects.get(price__date='2014-06-06').close, 10)

    def test_parser(self):
        self.assertEqual(parse_splits([{'date': '2020-08-31', 'split': '3/2'}]),
                         {date(2020, 8, 31): Decimal('1.5')})
        for value in [{}, [{'date': 'bad', 'split': '4/1'}],
                      [{'date': '2020-08-31', 'split': v} for v in ['0/1']],
                      [{'date': '2020-08-31', 'split': 'NaN/1'}],
                      [{'date': '2020-08-31', 'split': '1/0'}]]:
            with self.assertRaises(ValueError):
                parse_splits(value)

    @patch('stocks.management.commands.backfill_split_adjusted_prices.requests.get', side_effect=AssertionError('Forex must not call splits API'))
    def test_forex_rebuilds_without_request_or_adjustment(self, request):
        raw = Price.objects.create(ticker='XAGUSD.FOREX', date='2026-10-30',
            open=31, high=33, low=30, close=32, volume=0, dividends=0, stock_splits=0)
        StockSplit.objects.create(ticker=raw.ticker, date='2026-10-31', ratio=4)
        call_command('backfill_split_adjusted_prices', tickers=[raw.ticker], api_token='test', stdout=StringIO())
        adjusted = SplitAdjustedPrice.objects.get(price=raw)
        self.assertEqual([adjusted.open, adjusted.high, adjusted.low, adjusted.close], [31, 33, 30, 32])
        self.assertEqual(adjusted.adjustment_factor, 1)
        self.assertFalse(StockSplit.objects.filter(ticker=raw.ticker).exists())
        request.assert_not_called()

    @patch('stocks.management.commands.backfill_split_adjusted_prices.requests.get', side_effect=AssertionError('Forex must not call splits API'))
    def test_forex_dry_run_preserves_existing_data(self, request):
        raw = Price.objects.create(ticker='EURUSD.FOREX', date='2026-10-30',
            open=1, high=1, low=1, close=1, volume=0, dividends=0, stock_splits=0)
        call_command('backfill_split_adjusted_prices', tickers=[raw.ticker], api_token='test', dry_run=True, stdout=StringIO())
        self.assertFalse(SplitAdjustedPrice.objects.filter(price=raw).exists())
        request.assert_not_called()

    @skipUnless(os.environ.get('SPLIT_SIMULATION_TEST') == '1', 'Requires local Postman simulation')
    def test_http_backfill(self):
        # Real HTTP against the documented-format Postman fixture, no HTTP stubs.
        StockSplit.objects.all().delete()
        call_command('backfill_split_adjusted_prices', tickers=['AAPL'], stdout=StringIO())
        self.assertEqual(StockSplit.objects.filter(ticker='AAPL').count(), 2)
        self.assertEqual(SplitAdjustedPrice.objects.get(price__date='2014-06-06').close, 10)
