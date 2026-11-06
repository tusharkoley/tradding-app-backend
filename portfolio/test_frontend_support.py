from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Asset, AssetCategory, AssetTransaction, AuditLog, Portfolio


class FrontendSupportTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='ui@example.test', is_active=True, is_staff=False)
        self.other = get_user_model().objects.create_user(email='other-ui@example.test', is_active=True, is_staff=False)
        self.portfolio = Portfolio.objects.create(owner=self.user, name='Personal')
        self.category = AssetCategory.objects.get(code='property')
        self.asset = Asset.objects.create(portfolio=self.portfolio, category=self.category, name='Home')
        self.client.force_authenticate(user=self.user)
        self.stamp = timezone.now().replace(microsecond=0) - timedelta(minutes=1)

    def valuation(self, **changes):
        body = dict(valued_at=self.stamp.isoformat(), basis='unit', value='20', source='Statement')
        body.update(changes)
        return self.client.post(f'/portfolio/assets/{self.asset.pk}/valuations/', body, format='json')

    def opening(self, **changes):
        body = dict(asset=self.asset.pk, kind='opening', transaction_date=str(timezone.localdate()), quantity='2', amount='10')
        body.update(changes)
        return self.client.post('/portfolio/transactions/', body, format='json')

    def test_atomic_asset_registration_with_opening_and_valuation(self):
        body = dict(portfolio=self.portfolio.pk, category=self.category.pk, name='New property', currency='GBP',
            metadata={'property_type': 'Flat', 'location': 'London'},
            opening_transaction={'transaction_date': str(timezone.localdate()), 'quantity': '1', 'amount': '200000'},
            initial_valuation={'valued_at': self.stamp.isoformat(), 'basis': 'total', 'value': '220000'})
        response = self.client.post('/portfolio/assets/', body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        asset = Asset.objects.get(pk=response.data['id'])
        self.assertEqual(asset.current_quantity, Decimal('1'))
        self.assertEqual(asset.valuations.count(), 1)
        self.assertEqual(response.data['portfolio_name'], 'Personal')
        self.assertEqual(response.data['category_definition']['code'], 'property')

    def test_failed_nested_registration_rolls_back_asset_ledger_and_audit(self):
        counts = Asset.objects.count(), AssetTransaction.objects.count(), AuditLog.objects.count()
        body = dict(portfolio=self.portfolio.pk, category=self.category.pk, name='Invalid',
            opening_transaction={'transaction_date': str(timezone.localdate()), 'quantity': '1', 'amount': '10'},
            initial_valuation={'valued_at': (timezone.now() + timedelta(days=1)).isoformat(), 'basis': 'total', 'value': '10'})
        self.assertEqual(self.client.post('/portfolio/assets/', body, format='json').status_code, 400)
        self.assertEqual((Asset.objects.count(), AssetTransaction.objects.count(), AuditLog.objects.count()), counts)

    def test_opening_fields_cannot_be_used_to_overwrite_existing_asset(self):
        response = self.client.patch(f'/portfolio/assets/{self.asset.pk}/', {
            'opening_transaction': {'transaction_date': str(timezone.localdate()), 'quantity': '2', 'amount': '10'}}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.asset.transactions.count(), 0)

    def test_manual_valuations_are_revisioned_owned_and_audited(self):
        self.assertEqual(self.valuation().status_code, 201)
        second = self.valuation(value='21')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['revision'], 2)
        self.assertEqual(self.asset.valuations.count(), 2)
        self.assertEqual(AuditLog.objects.get(model_label='portfolio.assetvaluation', object_id=str(second.data['id'])).actor, self.user)
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.valuation().status_code, 404)
        self.assertEqual(self.client.get(f'/portfolio/assets/{self.asset.pk}/valuations/').status_code, 404)

    def test_valuation_input_rejects_future_negative_and_identity_injection(self):
        for changes in ({'value': '-1'}, {'valued_at': (timezone.now() + timedelta(days=1)).isoformat()},
                        {'method': 'market'}, {'asset': self.asset.pk}, {'currency': 'USD'}, {'revision': 9}):
            self.assertEqual(self.valuation(**changes).status_code, 400)
        self.assertEqual(self.asset.valuations.count(), 0)

    def test_holdings_read_recorded_unit_values_without_market_calls(self):
        self.opening()
        self.valuation()
        result = self.client.get('/portfolio/holdings/').data['results'][0]
        self.assertEqual(result['recorded_value'], '40.00000000')
        self.assertEqual(result['valuation_status'], 'recorded')
        self.assertEqual(result['category_name'], 'Property')
        self.assertEqual(result['portfolio_name'], 'Personal')

    def test_total_value_needs_review_after_holdings_change(self):
        self.opening()
        self.valuation(basis='total')
        self.assertEqual(self.client.get(f'/portfolio/assets/{self.asset.pk}/summary/').data['valuation_status'], 'recorded')
        self.opening(kind='buy', quantity='1', amount='5')
        summary = self.client.get(f'/portfolio/assets/{self.asset.pk}/summary/').data
        self.assertEqual(summary['valuation_status'], 'needs_review')
        self.assertIsNone(summary['recorded_value'])

    def test_unvalued_future_asof_and_archival_are_explicit(self):
        self.assertEqual(self.client.get('/portfolio/holdings/').data['results'][0]['valuation_status'], 'unvalued')
        self.opening()
        self.valuation()
        historical = self.client.get('/portfolio/holdings/', {'as_of': str(self.stamp.date() - timedelta(days=1))}).data['results'][0]
        self.assertEqual(historical['valuation_status'], 'unvalued')
        self.client.post(f'/portfolio/assets/{self.asset.pk}/archive/', {}, format='json')
        self.assertEqual(self.valuation().status_code, 400)

    def test_dynamic_fields_are_supplied_by_database_configuration(self):
        rows = self.client.get('/portfolio/categories/').data['results']
        by_code = {row['code']: row for row in rows}
        self.assertIn('exchange', by_code['stocks']['metadata_schema']['properties'])
        self.assertEqual(by_code['gold']['metadata_schema']['x-entry']['quantity_label'], 'Weight')
        self.assertIn('fund_name', by_code['pensions']['metadata_schema']['properties'])
        self.assertTrue(by_code['cash']['metadata_schema']['x-entry']['balance_mode'])

    def test_transaction_history_marks_already_reversed_entries(self):
        original = self.opening()
        self.client.post(f'/portfolio/transactions/{original.data["id"]}/reverse/', {'reason': 'Duplicate'}, format='json')
        history = self.client.get('/portfolio/transactions/').data['results']
        self.assertTrue(next(row for row in history if row['id'] == original.data['id'])['is_reversed'])
