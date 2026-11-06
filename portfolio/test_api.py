from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from .models import Asset, AssetCategory, AssetTransaction, AuditLog, Portfolio


class RegistryAPITests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(email='api-owner@example.com', password='Strong!Password92',
                                             is_active=True, is_staff=False)
        self.other = User.objects.create_user(email='api-other@example.com', password='Strong!Password92',
                                              is_active=True, is_staff=False)
        self.portfolio = Portfolio.objects.create(owner=self.user, name='Personal')
        self.foreign_portfolio = Portfolio.objects.create(owner=self.other, name='Private')
        self.category = AssetCategory.objects.get(code='stocks')
        self.asset = Asset.objects.create(portfolio=self.portfolio, category=self.category, name='Example shares')
        self.foreign_asset = Asset.objects.create(portfolio=self.foreign_portfolio, category=self.category, name='Secret shares')
        self.today = timezone.localdate()
        self.client.force_authenticate(user=self.user)

    def post_entry(self, **changes):
        payload = dict(asset=self.asset.pk, kind='buy', transaction_date=str(self.today),
                       quantity='10', unit_price='2', amount='20', fee='0.25')
        payload.update(changes)
        return self.client.post('/portfolio/transactions/', payload, format='json')

    def asset_payload(self, **changes):
        return dict(portfolio=self.portfolio.pk, category=self.category.pk,
                    name='New shares', currency='GBP', **changes)

    def test_all_resource_families_require_authentication(self):
        client = APIClient()
        for path in ('portfolios/', 'categories/', 'assets/', 'transactions/', 'holdings/',
                     f'assets/{self.asset.pk}/summary/'):
            with self.subTest(path=path):
                self.assertEqual(client.get('/portfolio/' + path).status_code, 401)
        self.assertEqual(client.post('/portfolio/portfolios/', {'name': 'No'}, format='json').status_code, 401)

    def test_existing_login_and_real_jwt_work_without_staff_permissions(self):
        client = APIClient()
        login = client.post('/users/login/', {'email': self.user.email, 'password': 'Strong!Password92'}, format='json')
        self.assertEqual(login.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {login.data["access"]}')
        self.assertEqual(client.get('/portfolio/portfolios/').status_code, 200)
        self.assertEqual(client.post('/portfolio/portfolios/', {'name': 'JWT'}, format='json').status_code, 201)
        client.credentials(HTTP_AUTHORIZATION='Bearer invalid-token')
        self.assertEqual(client.get('/portfolio/assets/').status_code, 401)

    def test_session_authentication_retains_csrf_enforcement(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.get('/portfolio/assets/').status_code, 200)
        self.assertEqual(client.post('/portfolio/portfolios/', {'name': 'CSRF'}, format='json').status_code, 403)

    def test_inactive_user_cannot_access_registry(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get('/portfolio/portfolios/').status_code, 403)

    def test_create_list_get_update_portfolio_and_audit_actor(self):
        response = self.client.post('/portfolio/portfolios/', {'name': 'Retirement', 'base_currency': 'INR'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(response.data['owner'], self.user.pk)
        detail = self.client.get(f'/portfolio/portfolios/{pk}/')
        self.assertEqual(detail.status_code, 200)
        changed = self.client.patch(f'/portfolio/portfolios/{pk}/', {'name': 'Pension'}, format='json')
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data['name'], 'Pension')
        result = self.client.get('/portfolio/portfolios/')
        self.assertEqual(result.data['count'], 2)
        self.assertNotIn(self.foreign_portfolio.pk, [row['id'] for row in result.data['results']])
        self.assertTrue(AuditLog.objects.filter(portfolio_id=pk, actor=self.user, action='update').exists())
        self.assertEqual(result['Cache-Control'], 'private, no-store')

    def test_portfolio_owner_and_base_currency_cannot_change(self):
        for body in ({'owner': self.other.pk}, {'base_currency': 'USD'}, {'unknown': 'bad'}):
            with self.subTest(body=body):
                response = self.client.patch(f'/portfolio/portfolios/{self.portfolio.pk}/', body, format='json')
                self.assertEqual(response.status_code, 400, response.data)

    def test_foreign_detail_ids_and_staff_bypass_are_rejected(self):
        for staff in (False, True):
            self.user.is_staff = self.user.is_superuser = staff
            self.user.save()
            for path in (f'portfolios/{self.foreign_portfolio.pk}/', f'assets/{self.foreign_asset.pk}/',
                         f'assets/{self.foreign_asset.pk}/summary/'):
                self.assertEqual(self.client.get('/portfolio/' + path).status_code, 404)
            self.assertEqual(self.client.patch(f'/portfolio/assets/{self.foreign_asset.pk}/', {'name': 'Hack'}, format='json').status_code, 404)
            self.assertEqual(self.client.post(f'/portfolio/assets/{self.foreign_asset.pk}/archive/', {}, format='json').status_code, 404)

    def test_category_registry_has_mutual_funds_schema_types_and_strategy(self):
        response = self.client.get('/portfolio/categories/')
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row['code']: row for row in response.data['results']}
        self.assertTrue({'stocks', 'etfs', 'gold', 'silver', 'property', 'pensions', 'mutual-funds', 'cash', 'other'} <= rows.keys())
        self.assertEqual(rows['cash']['supported_transaction_types'], ['deposit', 'withdrawal', 'opening', 'adjustment'])
        self.assertEqual(rows['property']['valuation_strategy'], 'manual')
        self.assertEqual(rows['gold']['metadata_schema']['properties']['purity']['maximum'], 1)
        self.assertEqual(self.client.get(f'/portfolio/categories/{self.category.pk}/').status_code, 200)
        self.assertEqual(self.client.post('/portfolio/categories/', {'name': 'No'}, format='json').status_code, 405)

    def test_new_database_category_drives_input_and_transaction_validation(self):
        custom = AssetCategory.objects.create(code='collectibles', name='Collectibles',
            valuation_strategy='manual', supported_transaction_types=['opening', 'adjustment'], metadata_schema={
                'type': 'object', 'properties': {'year': {'type': 'integer'}},
                'required': ['year'], 'additionalProperties': False,
            })
        payload = self.asset_payload()
        payload.update(category=custom.pk, metadata={'year': 'invalid'})
        self.assertEqual(self.client.post('/portfolio/assets/', payload, format='json').status_code, 400)
        payload['metadata'] = {'year': 1995}
        created = self.client.post('/portfolio/assets/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.post_entry(asset=created.data['id']).status_code, 400)
        self.assertEqual(self.post_entry(asset=created.data['id'], kind='opening').status_code, 201)
        self.assertEqual(self.client.get('/portfolio/categories/?search=collectibles').data['count'], 1)

    def test_asset_create_update_and_immutable_identity(self):
        created = self.client.post('/portfolio/assets/', self.asset_payload(), format='json')
        self.assertEqual(created.status_code, 201, created.data)
        url = f'/portfolio/assets/{created.data["id"]}/'
        self.assertEqual(self.client.get(url).status_code, 200)
        changed = self.client.patch(url, {'name': 'Renamed', 'metadata': {'isin': 'GB1234567890'}}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        other_owned = Portfolio.objects.create(owner=self.user, name='Second')
        for changes in ({'portfolio': other_owned.pk}, {'currency': 'USD'}, {'category': AssetCategory.objects.get(code='gold').pk},
                        {'unit': 'gram'}, {'market_symbol': 'CHANGED.US'}, {'is_archived': True}):
            self.assertEqual(self.client.patch(url, changes, format='json').status_code, 400)

    def test_foreign_relations_and_unknown_fields_are_rejected(self):
        payload = self.asset_payload()
        payload['portfolio'] = self.foreign_portfolio.pk
        self.assertEqual(self.client.post('/portfolio/assets/', payload, format='json').status_code, 400)
        self.assertEqual(self.post_entry(asset=self.foreign_asset.pk).status_code, 400)
        self.assertEqual(self.post_entry(reversal_of=1).status_code, 400)
        self.assertEqual(self.post_entry(correction_of=1).status_code, 400)
        self.assertEqual(self.post_entry(owner=self.other.pk).status_code, 400)
        self.assertEqual(self.post_entry(allow_short=True).status_code, 400)

    def test_category_specific_metadata_and_valuation_strategy(self):
        gold = AssetCategory.objects.get(code='gold')
        for metadata in ({'purity': 1.1}, {'unexpected': True}, []):
            payload = self.asset_payload()
            payload.update(category=gold.pk, metadata=metadata)
            self.assertEqual(self.client.post('/portfolio/assets/', payload, format='json').status_code, 400)
        payload = self.asset_payload()
        payload.update(category=AssetCategory.objects.get(code='property').pk,
                       valuation_method='market', market_symbol='HOUSE')
        self.assertEqual(self.client.post('/portfolio/assets/', payload, format='json').status_code, 400)

    def test_inactive_category_cannot_be_assigned_but_existing_assets_can_be_updated(self):
        self.category.is_active = False
        self.category.save()
        self.assertEqual(self.client.post('/portfolio/assets/', self.asset_payload(), format='json').status_code, 400)
        self.assertEqual(self.client.get(f'/portfolio/categories/{self.category.pk}/').status_code, 404)
        self.assertEqual(self.client.patch(f'/portfolio/assets/{self.asset.pk}/', {'name': 'Still editable'}, format='json').status_code, 200)

    def test_pagination_filter_search_and_ordering(self):
        Asset.objects.create(portfolio=self.portfolio, category=self.category, name='Alpha')
        response = self.client.get('/portfolio/assets/?page_size=1&ordering=name')
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['results'][0]['name'], 'Alpha')
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(self.client.get('/portfolio/assets/?search=Alpha').data['count'], 1)
        self.assertEqual(self.client.get('/portfolio/assets/?category_code=stocks&currency=GBP').data['count'], 2)
        self.assertEqual(self.client.get(f'/portfolio/assets/?portfolio={self.foreign_portfolio.pk}').data['count'], 0)
        self.assertEqual(self.client.get('/portfolio/portfolios/?base_currency=INR').data['count'], 0)

    def test_invalid_filters_are_explicit_errors(self):
        for query in ('portfolio=1.5', 'portfolio=-1', 'is_archived=invalid', 'currency=EUR', 'unknown=1'):
            self.assertEqual(self.client.get('/portfolio/assets/?' + query).status_code, 400)
        self.assertEqual(self.client.get('/portfolio/transactions/?date_from=2025-02-01&date_to=2025-01-01').status_code, 400)
        self.assertEqual(self.client.get('/portfolio/holdings/?as_of=bad').status_code, 400)

    def test_record_transactions_and_paginated_history(self):
        bought = self.post_entry()
        self.assertEqual(bought.status_code, 201, bought.data)
        self.assertEqual(bought.data['currency'], 'GBP')
        self.assertEqual(bought.data['quantity'], '10.000000000000')
        sold = self.post_entry(kind='sell', quantity='-2', amount='-4', notes='Trim position')
        self.assertEqual(sold.status_code, 201, sold.data)
        history = self.client.get(f'/portfolio/transactions/?asset={self.asset.pk}&kind=sell&search=Trim')
        self.assertEqual(history.data['count'], 1)
        self.assertEqual(self.client.get(f'/portfolio/transactions/{bought.data["id"]}/').status_code, 200)
        event = AuditLog.objects.get(model_label='portfolio.assettransaction', object_id=str(bought.data['id']))
        self.assertEqual(event.actor, self.user)
        self.assertEqual(self.asset.current_quantity, Decimal('8'))

    def test_invalid_financial_input_leaves_no_audit_or_ledger(self):
        count = AuditLog.objects.count()
        for changes in ({'quantity': '0'}, {'quantity': '-1'}, {'unit_price': '-1'}, {'amount': '21'},
                        {'fee': '-0.1'}, {'quantity': 'NaN'}, {'currency': 'USD'}, {'quantity': '1.0000000000001'},
                        {'kind': 'adjustment', 'notes': ''}, {'kind': 'deposit'}):
            with self.subTest(changes=changes):
                response = self.post_entry(**changes)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.asset.transactions.count(), 0)
        self.assertEqual(AuditLog.objects.count(), count)

    def test_cash_deposit_and_withdrawal_follow_category_configuration(self):
        cash = Asset.objects.create(portfolio=self.portfolio, category=AssetCategory.objects.get(code='cash'),
                                    name='Cash', unit='GBP')
        self.assertEqual(self.post_entry(asset=cash.pk, kind='deposit', quantity='100', unit_price='1', amount='100').status_code, 201)
        self.assertEqual(self.post_entry(asset=cash.pk, kind='withdrawal', quantity='-20', unit_price='1', amount='-20').status_code, 201)
        self.assertEqual(self.post_entry(asset=cash.pk, kind='withdrawal', quantity='-81', unit_price='1', amount='-81').status_code, 400)
        self.assertEqual(self.post_entry(asset=cash.pk, kind='deposit', quantity='10', unit_price=None, amount='20').status_code, 400)
        self.assertEqual(cash.current_quantity, Decimal('80'))

    def test_sale_exceeding_holdings_is_rejected_atomically(self):
        self.post_entry()
        events = AuditLog.objects.count()
        response = self.post_entry(kind='sell', quantity='-11', amount='-22')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('quantity', response.data)
        self.assertEqual(self.asset.transactions.count(), 1)
        self.assertEqual(AuditLog.objects.count(), events)

    def test_backdated_sale_is_rejected_even_if_current_holdings_cover_it(self):
        self.post_entry()
        response = self.post_entry(kind='sell', quantity='-1', amount='-2',
                                   transaction_date=str(self.today - timedelta(days=1)))
        self.assertEqual(response.status_code, 400, response.data)

    def test_future_purchase_cannot_fund_todays_sale(self):
        self.assertEqual(self.post_entry(transaction_date=str(self.today + timedelta(days=1))).status_code, 201)
        self.assertEqual(self.post_entry(kind='sell', quantity='-1', amount='-2').status_code, 400)
        self.assertEqual(self.asset.current_quantity, Decimal('0'))

    def test_summary_and_holdings_are_exact_and_owner_scoped(self):
        self.post_entry(quantity='0.1', unit_price='10', amount='1')
        self.post_entry(quantity='0.2', unit_price='10', amount='2',
                        transaction_date=str(self.today + timedelta(days=1)))
        summary = self.client.get(f'/portfolio/assets/{self.asset.pk}/summary/')
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data['quantity'], '0.100000000000')
        self.assertEqual(summary.data['net_recorded_amount'], '1.00000000')
        self.assertEqual(summary.data['net_fees'], '0.25000000')
        holdings = self.client.get('/portfolio/holdings/')
        self.assertEqual(holdings.data['count'], 1)
        self.assertEqual(holdings.data['results'][0]['asset'], self.asset.pk)
        future = self.client.get('/portfolio/holdings/', {'as_of': str(self.today + timedelta(days=1))})
        self.assertEqual(future.data['results'][0]['quantity'], '0.300000000000')

    def test_archive_is_idempotent_and_retains_holdings_and_repair_access(self):
        bought = self.post_entry()
        url = f'/portfolio/assets/{self.asset.pk}/archive/'
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 200)
        events = AuditLog.objects.count()
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 200)
        self.assertEqual(AuditLog.objects.count(), events)
        self.assertEqual(self.post_entry().status_code, 400)
        self.assertEqual(self.client.get('/portfolio/assets/?is_archived=false').data['count'], 0)
        self.assertEqual(self.client.get('/portfolio/holdings/').data['results'][0]['quantity'], '10.000000000000')
        response = self.client.post(f'/portfolio/transactions/{bought.data["id"]}/reverse/', {'reason': 'Duplicate'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_archived_portfolio_rejects_new_assets_and_transactions(self):
        self.client.patch(f'/portfolio/portfolios/{self.portfolio.pk}/', {'is_archived': True}, format='json')
        self.assertEqual(self.client.post('/portfolio/assets/', self.asset_payload(), format='json').status_code, 400)
        self.assertEqual(self.post_entry().status_code, 400)

    def test_reversal_requires_reason_is_exact_and_cannot_repeat(self):
        bought = self.post_entry()
        url = f'/portfolio/transactions/{bought.data["id"]}/reverse/'
        self.assertEqual(self.client.post(url, {'reason': '  '}, format='json').status_code, 400)
        reversed_entry = self.client.post(url, {'reason': 'Duplicate import'}, format='json')
        self.assertEqual(reversed_entry.status_code, 201, reversed_entry.data)
        self.assertEqual(reversed_entry.data['reversal_of'], bought.data['id'])
        self.assertEqual(reversed_entry.data['quantity'], '-10.000000000000')
        self.assertEqual(reversed_entry.data['fee'], '-0.25000000')
        self.assertEqual(self.client.post(url, {'reason': 'Again'}, format='json').status_code, 400)
        self.assertEqual(self.asset.current_quantity, Decimal('0'))

    def test_reversal_of_purchase_with_dependent_sale_is_rejected(self):
        bought = self.post_entry()
        self.post_entry(kind='sell', quantity='-5', amount='-10')
        events = AuditLog.objects.count()
        result = self.client.post(f'/portfolio/transactions/{bought.data["id"]}/reverse/', {'reason': 'Error'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertEqual(self.asset.transactions.count(), 2)
        self.assertEqual(AuditLog.objects.count(), events)

    def test_correction_validates_combined_ledger_and_preserves_links(self):
        bought = self.post_entry()
        self.post_entry(kind='sell', quantity='-5', amount='-10')
        url = f'/portfolio/transactions/{bought.data["id"]}/correct/'
        response = self.client.post(url, {'reason': 'Quantity typo', 'changes': {'quantity': '8', 'amount': '16'}}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['replacement']['correction_of'], bought.data['id'])
        self.assertEqual(self.asset.current_quantity, Decimal('3'))
        self.assertEqual(AssetTransaction.objects.get(pk=bought.data['id']).quantity, Decimal('10'))
        second = self.client.post(f'/portfolio/transactions/{response.data["replacement"]["id"]}/correct/',
                                  {'reason': 'Second correction', 'changes': {'quantity': '7', 'amount': '14'}}, format='json')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(self.asset.current_quantity, Decimal('2'))

    def test_invalid_correction_rolls_back_all_records_and_audit(self):
        bought = self.post_entry()
        self.post_entry(kind='sell', quantity='-5', amount='-10')
        events = AuditLog.objects.count()
        url = f'/portfolio/transactions/{bought.data["id"]}/correct/'
        for changes in ({'quantity': '4', 'amount': '8'}, {'amount': '999'},
                        {'transaction_date': str(self.today + timedelta(days=1))},
                        {'asset': self.foreign_asset.pk}, {}, {'fee': '-1'}):
            with self.subTest(changes=changes):
                response = self.client.post(url, {'reason': 'Invalid', 'changes': changes}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.asset.transactions.count(), 2)
                self.assertEqual(AuditLog.objects.count(), events)

    def test_foreign_transaction_history_and_actions_are_inaccessible(self):
        self.client.force_authenticate(user=self.other)
        foreign = self.post_entry(asset=self.foreign_asset.pk)
        self.assertEqual(foreign.status_code, 201)
        self.client.force_authenticate(user=self.user)
        pk = foreign.data['id']
        self.assertEqual(self.client.get('/portfolio/transactions/').data['count'], 0)
        self.assertEqual(self.client.get(f'/portfolio/transactions/{pk}/').status_code, 404)
        for action, body in (('reverse', {'reason': 'Hack'}), ('correct', {'reason': 'Hack', 'changes': {'notes': 'Hack'}})):
            self.assertEqual(self.client.post(f'/portfolio/transactions/{pk}/{action}/', body, format='json').status_code, 404)

    def test_transactions_cannot_be_patched_or_deleted(self):
        bought = self.post_entry()
        url = f'/portfolio/transactions/{bought.data["id"]}/'
        self.assertEqual(self.client.patch(url, {'quantity': '100'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(self.client.delete(f'/portfolio/assets/{self.asset.pk}/').status_code, 405)
        self.assertEqual(self.client.delete(f'/portfolio/portfolios/{self.portfolio.pk}/').status_code, 405)

    def test_direct_model_writer_cannot_bypass_no_short_rule(self):
        with self.assertRaisesMessage(ValidationError, 'Insufficient holdings'):
            AssetTransaction.objects.create(asset=self.asset, kind='sell', transaction_date=self.today,
                quantity=Decimal('-1'), amount=Decimal('-2'), currency='GBP')
        self.assertEqual(self.asset.transactions.count(), 0)
