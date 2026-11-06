from datetime import date, timedelta
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .admin import AssetAdmin, AssetTransactionAdmin, AuditLogAdmin, PortfolioAdmin
from .models import (Asset, AssetCategory, AssetTransaction, AssetValuation, AuditLog,
                     CurrencyExchangeRate, Portfolio, PortfolioSnapshot)
from .services import correct_transaction, reverse_transaction


class PortfolioModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(email='owner@example.com', password='test',
                                             is_active=True, is_staff=False)
        self.other = User.objects.create_user(email='other@example.com', password='test',
                                              is_active=True, is_staff=False)
        self.portfolio = Portfolio.objects.create(owner=self.user, name='Personal')
        self.other_portfolio = Portfolio.objects.create(owner=self.other, name='Other')
        self.category = AssetCategory.objects.get(code='stocks')
        self.asset = Asset.objects.create(portfolio=self.portfolio, category=self.category,
                                          name='Example', currency='GBP')
        self.other_asset = Asset.objects.create(portfolio=self.other_portfolio, category=self.category,
                                                name='Other example', currency='GBP')
        self.today = timezone.localdate()

    def entry(self, **changes):
        payload = dict(asset=self.asset, kind='buy', transaction_date=self.today,
                       quantity=Decimal('2'), unit_price=Decimal('10'), amount=Decimal('20'),
                       fee=Decimal('0.25'), currency='GBP')
        payload.update(changes)
        entry = AssetTransaction(**payload)
        entry.save(actor=self.user)
        return entry

    def valuation(self, **changes):
        payload = dict(asset=self.asset, valued_at=timezone.now(), method='manual',
                       value=Decimal('10'), currency='GBP', source='statement')
        payload.update(changes)
        return AssetValuation.objects.create(**payload)

    def snapshot(self, **changes):
        payload = dict(portfolio=self.portfolio, snapshot_date=self.today, currency='GBP',
                       total_assets=Decimal('100'), total_liabilities=Decimal('20'),
                       net_value=Decimal('80'), calculation_version='v1',
                       inputs={'valuation_ids': [1], 'fx_rate_ids': []})
        payload.update(changes)
        return PortfolioSnapshot.objects.create(**payload)

    def test_multiple_portfolios_use_existing_user(self):
        Portfolio.objects.create(owner=self.user, name='Pension', base_currency='INR')
        self.assertEqual(self.user.tracker_portfolios.count(), 2)

    def test_seeded_categories_include_all_asset_classes_and_liabilities(self):
        self.assertEqual(AssetCategory.objects.count(), 10)
        self.assertTrue(AssetCategory.objects.get(code='liabilities').is_liability)

    def test_all_supported_currencies_and_reject_unknown(self):
        for currency in ('GBP', 'USD', 'INR'):
            Portfolio.objects.create(owner=self.user, name=currency, base_currency=currency)
        with self.assertRaises(ValidationError):
            Portfolio.objects.create(owner=self.user, name='Invalid', base_currency='EUR')

    def test_inactive_owner_is_rejected(self):
        self.user.is_active = False
        self.user.save()
        with self.assertRaises(ValidationError):
            Portfolio.objects.create(owner=self.user, name='Invalid')

    def test_ownership_scopes_every_private_model_and_anonymous(self):
        self.entry()
        self.valuation()
        self.snapshot()
        for model in (Portfolio, Asset, AssetTransaction, AssetValuation, PortfolioSnapshot, AuditLog):
            self.assertTrue(model.objects.for_user(self.user).exists())
            self.assertFalse(model.objects.for_user(AnonymousUser()).exists())
        self.assertEqual(list(Asset.objects.for_user(self.user)), [self.asset])
        self.assertNotIn(self.portfolio, Portfolio.objects.for_user(self.other))

    def test_actor_cannot_write_another_users_data(self):
        self.asset.name = 'Unauthorized'
        with self.assertRaises(PermissionDenied):
            self.asset.save(actor=self.other)

    def test_owner_currency_and_asset_identity_cannot_be_reassigned(self):
        for field, value in [('owner', self.other), ('base_currency', 'USD')]:
            portfolio = Portfolio.objects.get(pk=self.portfolio.pk)
            setattr(portfolio, field, value)
            with self.assertRaises(ValidationError):
                portfolio.save()
        self.asset.portfolio = self.other_portfolio
        with self.assertRaises(ValidationError):
            self.asset.save()

    def test_metadata_schema_validation_and_extension(self):
        category = AssetCategory.objects.create(code='collectibles', name='Collectibles', metadata_schema={
            'type': 'object', 'properties': {'year': {'type': 'integer'}},
            'required': ['year'], 'additionalProperties': False,
        })
        asset = Asset(portfolio=self.portfolio, category=category, name='Painting', metadata={'year': 'bad'})
        with self.assertRaises(ValidationError):
            asset.save()
        asset.metadata = {'year': 1990}
        asset.save()
        category.metadata_schema = {}
        with self.assertRaises(ValidationError):
            category.save()

    def test_non_object_metadata_and_remote_schema_refs_are_rejected(self):
        for metadata in ([], 'text', 4):
            self.asset.metadata = metadata
            with self.assertRaises(ValidationError):
                self.asset.save()
        with self.assertRaises(ValidationError):
            AssetCategory.objects.create(code='invalid', name='Invalid',
                                         metadata_schema={'$ref': 'https://example.com/schema'})

    def test_market_method_requires_instrument_identity(self):
        self.asset.valuation_method = 'market'
        with self.assertRaises(ValidationError):
            self.asset.save()
        market = Asset.objects.create(portfolio=self.portfolio, category=self.category,
                                      name='Market', valuation_method='market', market_symbol='ABC.US')
        self.assertEqual(market.valuation_method, 'market')

    def test_signed_ledger_and_as_of_holdings(self):
        self.entry(kind='opening', quantity=Decimal('1'), amount=Decimal('10'),
                   transaction_date=self.today - timedelta(days=1))
        self.entry()
        self.entry(kind='sell', quantity=Decimal('-0.5'), amount=Decimal('-5'))
        self.entry(transaction_date=self.today + timedelta(days=1))
        self.assertEqual(self.asset.current_quantity, Decimal('2.5'))
        self.assertEqual(self.asset.quantity_as_of(self.today - timedelta(days=1)), Decimal('1'))
        self.assertEqual(self.asset.quantity_as_of(), Decimal('4.5'))

    def test_fractional_quantities_remain_decimal(self):
        for _ in range(3):
            self.entry(quantity=Decimal('0.1'), amount=Decimal('1'))
        self.assertEqual(self.asset.current_quantity, Decimal('0.3'))

    def test_cash_deposit_withdrawal_and_explained_adjustment(self):
        cash = Asset.objects.create(portfolio=self.portfolio, category=AssetCategory.objects.get(code='cash'),
                                    name='GBP cash', currency='GBP', unit='GBP')
        self.entry(asset=cash, kind='deposit', quantity=Decimal('10'), unit_price=Decimal('1'), amount=Decimal('10'))
        self.entry(asset=cash, kind='withdrawal', quantity=Decimal('-2'), unit_price=Decimal('1'), amount=Decimal('-2'))
        self.entry(asset=cash, kind='adjustment', quantity=Decimal('1'), unit_price=Decimal('1'),
                   amount=Decimal('1'), notes='Reconcile statement')
        self.assertEqual(cash.current_quantity, Decimal('9'))

    def test_transaction_validation(self):
        for changes in ({'kind': 'sell'}, {'kind': 'invalid'}, {'currency': 'USD'},
                        {'fee': Decimal('-1')}, {'amount': Decimal('21')},
                        {'unit_price': Decimal('-10')}, {'kind': 'adjustment', 'notes': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.entry(**changes)

    def test_transaction_kinds_follow_category_policy(self):
        for kind in ('deposit', 'withdrawal'):
            with self.assertRaises(ValidationError):
                self.entry(kind=kind)
        cash = Asset.objects.create(portfolio=self.portfolio, category=AssetCategory.objects.get(code='cash'),
                                    name='Cash', currency='GBP')
        with self.assertRaises(ValidationError):
            self.entry(asset=cash, kind='buy')
        with self.assertRaises(ValidationError):
            self.entry(asset=cash, kind='deposit')

    def test_creation_timestamp_is_immutable(self):
        self.asset.created_at -= timedelta(days=1)
        with self.assertRaises(ValidationError):
            self.asset.save()

    def test_transaction_date_is_independent_of_creation_time(self):
        past = date(2020, 1, 1)
        entry = self.entry(transaction_date=past)
        self.assertEqual(entry.transaction_date, past)
        self.assertGreater(entry.created_at.date(), past)

    def test_reverse_once_preserves_original_and_actor(self):
        entry = self.entry()
        reversed_entry = reverse_transaction(user=self.user, entry_id=entry.pk, reason='Duplicate import')
        self.assertEqual(self.asset.current_quantity, Decimal('0'))
        self.assertEqual(reversed_entry.fee, Decimal('-0.25'))
        entry.refresh_from_db()
        self.assertEqual(entry.quantity, Decimal('2'))
        event = AuditLog.objects.get(model_label='portfolio.assettransaction', object_id=str(reversed_entry.pk))
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.reason, 'Duplicate import')
        with self.assertRaises(ValidationError):
            reverse_transaction(user=self.user, entry_id=entry.pk, reason='Again')
        with self.assertRaises(ValidationError):
            reverse_transaction(user=self.user, entry_id=reversed_entry.pk, reason='Again')

    def test_reversal_rejects_wrong_asset_or_values_or_date(self):
        original = self.entry()
        for changes in ({'asset': self.other_asset}, {'quantity': Decimal('-3')},
                        {'transaction_date': self.today - timedelta(days=1)}, {'notes': ''}):
            payload = dict(kind='buy', quantity=Decimal('-2'), amount=Decimal('-20'),
                           fee=Decimal('-0.25'), reversal_of=original, notes='Reason')
            payload.update(changes)
            with self.assertRaises((ValidationError, PermissionDenied)):
                self.entry(**payload)

    def test_correction_is_linked_and_atomic(self):
        original = self.entry()
        reverse, replacement = correct_transaction(user=self.user, entry_id=original.pk,
            reason='Quantity typo', changes={'quantity': Decimal('3'), 'amount': Decimal('30')})
        self.assertEqual(replacement.correction_of, original)
        self.assertEqual(reverse.reversal_of, original)
        self.assertEqual(self.asset.current_quantity, Decimal('3'))

    def test_failed_correction_rolls_back_reversal_and_audit(self):
        original = self.entry()
        events = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            correct_transaction(user=self.user, entry_id=original.pk, reason='Invalid', changes={'amount': Decimal('999')})
        self.assertEqual(self.asset.transactions.count(), 1)
        self.assertEqual(AuditLog.objects.count(), events)

    def test_cross_user_and_anonymous_cannot_reverse(self):
        original = self.entry()
        for user in (self.other, AnonymousUser()):
            with self.assertRaises(AssetTransaction.DoesNotExist):
                reverse_transaction(user=user, entry_id=original.pk, reason='Unauthorized')

    def test_ledger_valuations_snapshots_and_rates_are_immutable(self):
        rate = CurrencyExchangeRate.objects.create(base_currency='GBP', quote_currency='INR',
            rate_date=self.today, rate=Decimal('105'), source='fixture')
        for obj in (self.entry(), self.valuation(), self.snapshot(), rate):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ValidationError):
                    obj.save()
                with self.assertRaises(ValidationError):
                    obj.delete()

    def test_valuation_revisions_and_currency(self):
        original = self.valuation()
        replacement = self.valuation(valued_at=original.valued_at, revision=2, value=Decimal('12'), basis='total')
        self.assertEqual(self.asset.valuations.count(), 2)
        self.assertEqual(replacement.value, Decimal('12'))
        with self.assertRaises(ValidationError):
            self.valuation(valued_at=original.valued_at)
        with self.assertRaises(ValidationError):
            self.valuation(currency='USD')

    def test_fx_rates_are_positive_dated_unique_and_revisioned(self):
        payload = dict(base_currency='GBP', quote_currency='USD', rate_date=self.today,
                       rate=Decimal('1.25'), source='fixture')
        CurrencyExchangeRate.objects.create(**payload)
        CurrencyExchangeRate.objects.create(**payload, revision=2)
        for changes in ({}, {'rate': Decimal('0')}, {'quote_currency': 'GBP'}, {'quote_currency': 'EUR'}):
            with self.assertRaises(ValidationError):
                CurrencyExchangeRate.objects.create(**(payload | changes))

    def test_snapshot_net_value_currency_and_revision(self):
        self.snapshot()
        self.snapshot(revision=2)
        for changes in ({}, {'revision': 0}, {'revision': 3, 'net_value': Decimal('99')},
                        {'revision': 3, 'currency': 'USD'}):
            with self.assertRaises(ValidationError):
                self.snapshot(**changes)

    def test_audit_before_after_and_no_bulk_bypasses(self):
        self.asset.name = 'Renamed'
        self.asset.save(actor=self.user, reason='Display name')
        event = AuditLog.objects.filter(model_label='portfolio.asset', action='update').get()
        self.assertEqual(event.before['name'], 'Example')
        self.assertEqual(event.after['name'], 'Renamed')
        for operation in (lambda: Asset.objects.update(name='Bypass'), lambda: Asset.objects.all().delete(),
                          lambda: Asset.objects.bulk_create([]), lambda: Asset.objects.bulk_update([], ['name']),
                          lambda: event.save(), lambda: event.delete()):
            with self.assertRaises(ValidationError):
                operation()

    def test_explicit_primary_key_cannot_overwrite_history(self):
        entry = self.entry()
        fake = AssetTransaction(pk=entry.pk, asset=self.asset, kind='buy', transaction_date=self.today,
                                quantity=Decimal('1'), amount=Decimal('1'), currency='GBP')
        with self.assertRaises(ValidationError):
            fake.save()
        event = AuditLog.objects.first()
        with self.assertRaises(ValidationError):
            AuditLog(pk=event.pk, model_label='fake', object_id='1', action='create', after={'fake': True}).save()

    def test_deletion_protection_and_archive_preserve_history(self):
        self.entry()
        with self.assertRaises(ProtectedError):
            self.user.delete()
        self.asset.is_archived = True
        self.asset.save(actor=self.user)
        self.assertEqual(self.asset.current_quantity, Decimal('2'))
        with self.assertRaises(ValidationError):
            self.portfolio.delete()

    def test_database_constraints_apply_without_model_validation(self):
        entry = self.entry()
        # _base_manager intentionally bypasses the application guard to test SQL constraints.
        for changes in ({'quantity': Decimal('-1')}, {'currency': 'EUR'}, {'fee': Decimal('-1')}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                AssetTransaction._base_manager.filter(pk=entry.pk).update(**changes)

    def test_admin_filters_even_superusers_and_relation_choices(self):
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        request = RequestFactory().get('/admin/')
        request.user = self.user
        site = AdminSite()
        portfolio_admin = PortfolioAdmin(Portfolio, site)
        self.assertEqual(list(portfolio_admin.get_queryset(request)), [self.portfolio])
        self.assertFalse(portfolio_admin.has_view_permission(request, self.other_portfolio))
        asset_admin = AssetAdmin(Asset, site)
        field = asset_admin.formfield_for_foreignkey(Asset._meta.get_field('portfolio'), request)
        self.assertEqual(list(field.queryset), [self.portfolio])
        entry = self.entry()
        self.assertFalse(AssetTransactionAdmin(AssetTransaction, site).has_change_permission(request, entry))
        audit_admin = AuditLogAdmin(AuditLog, site)
        foreign_event = AuditLog.objects.filter(portfolio=self.other_portfolio).first()
        self.assertNotIn(foreign_event, audit_admin.get_queryset(request))
        self.assertFalse(audit_admin.has_view_permission(request, foreign_event))

    def test_view_only_admin_cannot_reverse_transactions(self):
        self.user.is_staff = True
        self.user.save()
        self.user.user_permissions.add(Permission.objects.get(codename='view_assettransaction'))
        request = RequestFactory().get('/admin/')
        request.user = self.user
        model_admin = AssetTransactionAdmin(AssetTransaction, AdminSite())
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_reverse_permission(request))
        self.assertNotIn('reverse_entries', model_admin.get_actions(request))
