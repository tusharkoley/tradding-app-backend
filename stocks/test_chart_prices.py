from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from stocks.models import Price, StockSplit
from stocks.serializers import PriceSerilizer
from stocks.views import PriceList


class ChartPriceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        # Insert out of order to verify chronological chart output.
        self.after = Price.objects.create(ticker='EX.US', date=date(2026, 10, 27),
            open=25, high=28, low=24, close=27, volume=1000, dividends=0, stock_splits=4)
        self.before = Price.objects.create(ticker='EX.US', date=date(2026, 10, 26),
            open=100, high=112, low=96, close=108, volume=900, dividends=1, stock_splits=0)
        StockSplit.objects.create(ticker='EX.US', date=date(2026, 10, 27), ratio=Decimal('4'))

    def get_prices(self, ticker='EX.US'):
        return PriceList.as_view()(self.factory.get(f'/stocks/company/{ticker}/prices/'), ticker=ticker)

    def rebuild(self):
        call_command('backfill_split_adjusted_prices', tickers=['EX.US'], local_splits=True, stdout=StringIO())

    def test_split_boundary_shape_metadata_and_single_query(self):
        self.rebuild()
        with self.assertNumQueries(1):
            response = self.get_prices()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Price-Basis'], 'split-adjusted')
        self.assertEqual([row['id'] for row in response.data], [self.before.id, self.after.id])
        self.assertEqual(set(response.data[0]), set(PriceSerilizer(self.before).data))
        self.assertEqual([response.data[0][key] for key in ['open', 'high', 'low', 'close']], [25, 28, 24, 27])
        self.assertEqual(response.data[1]['close'], 27)
        self.assertEqual(response.data[0]['volume'], 900)
        self.assertEqual(response.data[0]['dividends'], 1)
        self.before.refresh_from_db()
        self.assertEqual(self.before.close, 108)

    def test_partial_adjustments_fail_instead_of_mixing_or_shortening(self):
        self.rebuild()
        self.after.split_adjusted_price.delete()
        response = self.get_prices()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('incomplete', response.data['detail'])

    def test_missing_history_retains_empty_response(self):
        response = self.get_prices('UNKNOWN.US')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
