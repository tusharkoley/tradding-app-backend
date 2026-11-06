"""Run against a disposable PostgreSQL database to establish row-lock guarantees."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from .models import Asset, AssetCategory, Portfolio
from .services import record_transaction, reverse_transaction


@skipUnless(connection.vendor == 'postgresql', 'Concurrent row locks require PostgreSQL.')
class ConcurrentLedgerTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='concurrent@example.com', is_active=True, is_staff=False)
        portfolio = Portfolio.objects.create(owner=self.user, name='Concurrent')
        # TransactionTestCase flushes seed data between methods, so create our own category.
        category = AssetCategory.objects.create(code='test-shares', name='Test shares')
        self.asset = Asset.objects.create(portfolio=portfolio, category=category, name='Shares')
        self.data = dict(kind='buy', transaction_date=date(2026, 1, 1), quantity=Decimal('10'),
                         unit_price=Decimal('2'), amount=Decimal('20'))
        self.original = record_transaction(user=self.user, asset_id=self.asset.pk, data=self.data)

    def run_concurrently(self, operation):
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
                return 'created'
            except ValidationError:
                return 'rejected'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_sales_cannot_both_consume_the_same_holdings(self):
        data = {**self.data, 'kind': 'sell', 'quantity': Decimal('-6'), 'amount': Decimal('-12')}
        results = self.run_concurrently(lambda: record_transaction(user=self.user, asset_id=self.asset.pk, data=data))
        self.assertCountEqual(results, ['created', 'rejected'])
        self.assertEqual(self.asset.current_quantity, Decimal('4'))
        self.assertEqual(self.asset.transactions.count(), 2)

    def test_concurrent_reversals_create_only_one_negation(self):
        results = self.run_concurrently(lambda: reverse_transaction(user=self.user, entry_id=self.original.pk, reason='Duplicate'))
        self.assertCountEqual(results, ['created', 'rejected'])
        self.assertEqual(self.asset.current_quantity, Decimal('0'))
        self.assertEqual(self.asset.transactions.count(), 2)
