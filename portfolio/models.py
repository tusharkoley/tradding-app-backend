"""Independent personal-asset ledger. No dependency on trading execution."""
import json
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from itertools import groupby

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from jsonschema import Draft202012Validator, SchemaError


class Currency(models.TextChoices):
    GBP = 'GBP', 'British pound'
    USD = 'USD', 'US dollar'
    INR = 'INR', 'Indian rupee'


def financial_field(**kwargs):
    return models.DecimalField(max_digits=28, decimal_places=8, **kwargs)


def quantity_field(**kwargs):
    return models.DecimalField(max_digits=28, decimal_places=12, **kwargs)


def validate_object(value):
    if not isinstance(value, dict):
        raise ValidationError('Metadata must be a JSON object.')


def validate_schema(value):
    validate_object(value)
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise ValidationError('Invalid JSON Schema.') from exc
    # Keep validation deterministic and offline: category schemas cannot fetch URLs.
    def check(node):
        if isinstance(node, dict):
            if '$ref' in node or '$dynamicRef' in node:
                raise ValidationError('Schema references are not supported; use inline definitions.')
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(value)


def default_transaction_types():
    return ['buy', 'sell', 'opening', 'adjustment']


def validate_transaction_types(value):
    allowed = {'buy', 'sell', 'deposit', 'withdrawal', 'opening', 'adjustment'}
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(value) != len(set(value))):
        raise ValidationError('Specify a nonempty, unique list of supported transaction types.')


class ProtectedQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Use instance.save() so validation and audit logging run.')

    def delete(self):
        raise ValidationError('Historical records cannot be deleted; archive or reverse them.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('Bulk writes bypass validation and audit logging.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('Bulk writes bypass validation and audit logging.')

    def for_user(self, user):
        if not user or not user.is_authenticated or not user.is_active:
            return self.none()
        return self.filter(**{self.model.owner_lookup: user.pk})


class HistoricalModel(models.Model):
    """Validated, audited writes; updates serialized to preserve the audit chain."""
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ProtectedQuerySet.as_manager()
    immutable = False
    owner_lookup = 'owner_id'

    class Meta:
        abstract = True

    def audit_state(self):
        return json.loads(json.dumps({
            field.attname: getattr(self, field.attname)
            for field in self._meta.concrete_fields
        }, cls=DjangoJSONEncoder))

    def audit_portfolio(self):
        return None

    def save(self, *args, actor=None, reason='', **kwargs):
        if kwargs.get('using') not in (None, 'default'):
            raise ValidationError('Portfolio records use the default database.')
        if args or kwargs.get('update_fields') is not None:
            raise ValidationError('Save the complete record using keyword arguments.')
        with transaction.atomic():
            previous = None
            if self._state.adding and self.pk is not None:
                raise ValidationError('Explicit primary keys are not supported for historical records.')
            if not self._state.adding:
                previous = type(self).objects.select_for_update().get(pk=self.pk)
                if self.immutable:
                    raise ValidationError('This record is immutable; append a correction instead.')
                if previous.created_at != self.created_at:
                    raise ValidationError('The system creation timestamp is immutable.')
            portfolio = self.audit_portfolio()
            if actor is not None:
                if not actor.is_authenticated or not actor.is_active:
                    raise PermissionDenied('An active authenticated actor is required.')
                if portfolio and portfolio.owner_id != actor.pk:
                    raise PermissionDenied('This portfolio belongs to another user.')
                if portfolio is None and not actor.is_superuser:
                    raise PermissionDenied('Only superusers can configure shared reference data.')
            self.full_clean()
            before = previous.audit_state() if previous else {}
            super().save(*args, **kwargs)
            AuditLog.objects.create(
                portfolio=portfolio, actor=actor, model_label=self._meta.label_lower,
                object_id=str(self.pk), action='update' if previous else 'create',
                before=before, after=self.audit_state(), reason=reason,
            )

    def delete(self, *args, **kwargs):
        raise ValidationError('Historical records cannot be deleted; archive or reverse them.')


class Portfolio(HistoricalModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name='tracker_portfolios')
    name = models.CharField(max_length=120)
    base_currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.GBP)
    is_archived = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['owner', 'is_archived'])]
        constraints = [models.CheckConstraint(condition=Q(base_currency__in=Currency.values),
                                               name='portfolio_valid_currency')]

    def audit_portfolio(self):
        return self

    def clean(self):
        if self.owner_id and not self.owner.is_active:
            raise ValidationError({'owner': 'An active user must own the portfolio.'})
        if not self._state.adding:
            old = Portfolio.objects.get(pk=self.pk)
            if old.owner_id != self.owner_id or old.base_currency != self.base_currency:
                raise ValidationError('Owner and base currency are immutable; create a new portfolio.')

    def __str__(self):
        return self.name


class AssetCategory(HistoricalModel):
    """Global reference data; version used schemas by creating another category."""
    class TransactionPolicy(models.TextChoices):
        POSITION = 'position', 'Position (buy/sell)'
        CASH = 'cash', 'Cash balance (deposit/withdrawal)'

    class ValuationStrategy(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        MARKET = 'market', 'Market data'
        FLEXIBLE = 'manual_or_market', 'Manual or market data'

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    is_liability = models.BooleanField(default=False)
    transaction_policy = models.CharField(max_length=8, choices=TransactionPolicy.choices,
                                          default=TransactionPolicy.POSITION)
    metadata_schema = models.JSONField(default=dict, blank=True, validators=[validate_schema])
    supported_transaction_types = models.JSONField(default=default_transaction_types,
                                                   validators=[validate_transaction_types])
    valuation_strategy = models.CharField(max_length=16, choices=ValuationStrategy.choices,
                                          default=ValuationStrategy.FLEXIBLE)
    is_active = models.BooleanField(default=True)

    def clean(self):
        if not self._state.adding and self.assets.exists():
            old = AssetCategory.objects.get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f)
                   for f in ('code', 'is_liability', 'transaction_policy', 'metadata_schema',
                             'supported_transaction_types', 'valuation_strategy')):
                raise ValidationError('Used category definitions are immutable; create a new category version.')

    def __str__(self):
        return self.name


class Asset(HistoricalModel):
    class ValuationMethod(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        MARKET = 'market', 'Market data'

    owner_lookup = 'portfolio__owner_id'
    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name='assets')
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name='assets')
    name = models.CharField(max_length=160)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.GBP)
    unit = models.CharField(max_length=30, default='unit', help_text='share, gram, troy ounce, GBP, etc.')
    company = models.ForeignKey('stocks.Company', null=True, blank=True, on_delete=models.PROTECT,
                                related_name='tracker_assets')
    market_symbol = models.CharField(max_length=50, blank=True)
    valuation_method = models.CharField(max_length=10, choices=ValuationMethod.choices,
                                        default=ValuationMethod.MANUAL)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_object])
    is_archived = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['portfolio', 'category']),
                   models.Index(fields=['portfolio', 'is_archived'])]
        constraints = [models.CheckConstraint(condition=Q(currency__in=Currency.values),
                                               name='asset_valid_currency')]

    def audit_portfolio(self):
        return self.portfolio

    def clean(self):
        validate_object(self.metadata)
        if self.category_id:
            validate_schema(self.category.metadata_schema)
            errors = list(Draft202012Validator(self.category.metadata_schema).iter_errors(self.metadata))
            if errors:
                raise ValidationError({'metadata': [error.message for error in errors]})
            if self._state.adding and not self.category.is_active:
                raise ValidationError({'category': 'Select an active category.'})
            strategy = self.category.valuation_strategy
            if strategy != AssetCategory.ValuationStrategy.FLEXIBLE and self.valuation_method != strategy:
                raise ValidationError({'valuation_method': 'This method is not supported by the category.'})
        if self.valuation_method == self.ValuationMethod.MARKET and not (self.market_symbol or self.company_id):
            raise ValidationError('Market-valued assets require a company or market symbol.')
        if not self._state.adding:
            old = Asset.objects.get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f) for f in
                   ('portfolio_id', 'category_id', 'currency', 'unit', 'company_id', 'market_symbol')):
                raise ValidationError('Asset identity is immutable; create a new asset.')

    def quantity_as_of(self, on_date=None):
        entries = self.transactions.all()
        if on_date is not None:
            entries = entries.filter(transaction_date__lte=on_date)
        # Sum Decimals in Python: SQLite NUMERIC aggregation uses binary floating point.
        with localcontext() as context:
            context.prec = 64
            return sum(entries.values_list('quantity', flat=True), Decimal('0'))

    def validate_holdings(self):
        """Call while holding this asset's row lock; dates have day-level precision."""
        rows = self.transactions.order_by('transaction_date', 'id').values_list('transaction_date', 'quantity')
        with localcontext() as context:
            context.prec = 64
            balance = Decimal('0')
            for effective_date, entries in groupby(rows.iterator(), key=lambda row: row[0]):
                balance += sum((quantity for _, quantity in entries), Decimal('0'))
                if balance < 0:
                    raise ValidationError({'quantity': f'Insufficient holdings on {effective_date}; short selling is not supported.'})

    @property
    def current_quantity(self):
        return self.quantity_as_of(timezone.localdate())

    def __str__(self):
        return self.name


class AssetTransaction(HistoricalModel):
    class Kind(models.TextChoices):
        BUY = 'buy', 'Buy'
        SELL = 'sell', 'Sell'
        DEPOSIT = 'deposit', 'Deposit'
        WITHDRAWAL = 'withdrawal', 'Withdrawal'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        OPENING = 'opening', 'Opening balance'

    immutable = True
    owner_lookup = 'asset__portfolio__owner_id'
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='transactions')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    transaction_date = models.DateField(db_index=True)
    quantity = quantity_field(help_text='Signed change in held units; liabilities use positive owed units.')
    unit_price = quantity_field(null=True, blank=True, validators=[MinValueValidator(0)])
    amount = financial_field(help_text='Signed book consideration, excluding fees; not portfolio cash flow.')
    fee = financial_field(default=Decimal('0'))
    currency = models.CharField(max_length=3, choices=Currency.choices)
    notes = models.TextField(blank=True)
    reversal_of = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT,
                                      related_name='reversal')
    correction_of = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT,
                                        related_name='correction')

    class Meta:
        indexes = [models.Index(fields=['asset', 'transaction_date', 'id'])]
        constraints = [
            models.CheckConstraint(condition=Q(currency__in=Currency.values), name='ptx_valid_currency'),
            models.CheckConstraint(condition=Q(unit_price__isnull=True) | Q(unit_price__gte=0),
                                   name='ptx_nonnegative_unit_price'),
            models.CheckConstraint(condition=(Q(reversal_of__isnull=True, fee__gte=0)
                                               | Q(reversal_of__isnull=False, fee__lte=0)),
                                   name='ptx_fee_direction'),
            models.CheckConstraint(condition=(Q(reversal_of__isnull=False)
                | Q(kind__in=['buy', 'deposit', 'opening'], quantity__gt=0, amount__gte=0)
                | Q(kind__in=['sell', 'withdrawal'], quantity__lt=0, amount__lte=0)
                | Q(kind='adjustment')), name='ptx_quantity_direction'),
            models.CheckConstraint(condition=~Q(id=models.F('reversal_of')), name='ptx_no_self_reversal'),
            models.CheckConstraint(condition=Q(reversal_of__isnull=True) | Q(correction_of__isnull=True),
                                   name='ptx_not_reversal_and_correction'),
            models.CheckConstraint(condition=~Q(id=models.F('correction_of')), name='ptx_no_self_correction'),
        ]

    def audit_portfolio(self):
        return self.asset.portfolio

    def save(self, *args, _validate_holdings=True, **kwargs):
        with transaction.atomic():
            # Serialize *every* supported writer, including admin and direct model saves.
            if self.asset_id:
                portfolio_id = Asset.objects.values_list('portfolio_id', flat=True).get(pk=self.asset_id)
                Portfolio.objects.select_for_update().get(pk=portfolio_id)
                self.asset = Asset.objects.select_for_update().get(pk=self.asset_id)
                if not (self.reversal_of_id or self.correction_of_id) and (
                        self.asset.is_archived or self.asset.portfolio.is_archived):
                    raise ValidationError('Cannot record new transactions on archived assets or portfolios.')
            super().save(*args, **kwargs)
            if _validate_holdings:
                self.asset.validate_holdings()

    def clean(self):
        if self.asset_id and self.currency != self.asset.currency:
            raise ValidationError({'currency': 'Transaction currency must match the asset.'})
        if self.asset_id:
            cash = self.asset.category.transaction_policy == AssetCategory.TransactionPolicy.CASH
            if self.kind not in self.asset.category.supported_transaction_types:
                raise ValidationError({'kind': 'Transaction kind is not appropriate for this category.'})
            if cash and (self.quantity != self.amount or self.unit_price not in (None, Decimal('1'))):
                raise ValidationError('Cash entries require amount equal to currency units and unit price 1 (or omitted).')
        if self.quantity == 0:
            raise ValidationError({'quantity': 'Quantity must be nonzero.'})
        if self.unit_price is not None and self.quantity is not None and self.amount is not None:
            with localcontext() as context:
                context.prec = 64
                expected = (self.quantity * self.unit_price).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_EVEN)
            if self.amount != expected:
                raise ValidationError({'amount': 'Amount must equal quantity × unit price, rounded to 8 places.'})
        if self.reversal_of_id:
            original = self.reversal_of
            if original.reversal_of_id:
                raise ValidationError('A reversal cannot itself be reversed; post a new corrected entry.')
            if (self.asset_id != original.asset_id or self.kind != original.kind
                    or self.currency != original.currency or self.unit_price != original.unit_price
                    or self.quantity != -original.quantity or self.amount != -original.amount
                    or self.fee != -original.fee):
                raise ValidationError('A reversal must exactly negate the original entry on the same asset.')
            if self.transaction_date < original.transaction_date:
                raise ValidationError('A reversal cannot precede the original transaction.')
            if not self.notes.strip():
                raise ValidationError({'notes': 'Explain the reversal.'})
        elif self.kind == self.Kind.ADJUSTMENT and not self.notes.strip():
            raise ValidationError({'notes': 'Explain the adjustment.'})
        if self.correction_of_id:
            original = self.correction_of
            if (original.reversal_of_id or original.asset_id != self.asset_id
                    or not AssetTransaction.objects.filter(reversal_of=original).exists()):
                raise ValidationError('Corrections require a reversed original entry on the same asset.')


class AssetValuation(HistoricalModel):
    class Basis(models.TextChoices):
        UNIT = 'unit', 'Price per unit'
        TOTAL = 'total', 'Total value of owned holding'

    immutable = True
    owner_lookup = 'asset__portfolio__owner_id'
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='valuations')
    valued_at = models.DateTimeField()
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    method = models.CharField(max_length=10, choices=Asset.ValuationMethod.choices)
    basis = models.CharField(max_length=5, choices=Basis.choices, default=Basis.UNIT)
    value = financial_field(validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, choices=Currency.choices)
    source = models.CharField(max_length=120)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=['asset', '-valued_at', '-revision'])]
        constraints = [
            models.UniqueConstraint(fields=['asset', 'valued_at', 'revision'], name='valuation_revision_unique'),
            models.CheckConstraint(condition=Q(value__gte=0), name='valuation_nonnegative'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='valuation_positive_revision'),
            models.CheckConstraint(condition=Q(currency__in=Currency.values), name='valuation_valid_currency'),
        ]

    def audit_portfolio(self):
        return self.asset.portfolio

    def clean(self):
        if self.asset_id and self.currency != self.asset.currency:
            raise ValidationError({'currency': 'Valuation currency must match the asset.'})


class CurrencyExchangeRate(HistoricalModel):
    immutable = True
    base_currency = models.CharField(max_length=3, choices=Currency.choices)
    quote_currency = models.CharField(max_length=3, choices=Currency.choices)
    rate_date = models.DateField()
    rate = quantity_field(validators=[MinValueValidator(Decimal('0.000000000001'))])
    source = models.CharField(max_length=120)
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        indexes = [models.Index(fields=['base_currency', 'quote_currency', '-rate_date'])]
        constraints = [
            models.UniqueConstraint(fields=['base_currency', 'quote_currency', 'rate_date', 'source', 'revision'],
                                    name='fx_revision_unique'),
            models.CheckConstraint(condition=Q(rate__gt=0), name='fx_positive_rate'),
            models.CheckConstraint(condition=~Q(base_currency=models.F('quote_currency')), name='fx_distinct_pair'),
            models.CheckConstraint(condition=Q(base_currency__in=Currency.values) & Q(quote_currency__in=Currency.values),
                                   name='fx_valid_currencies'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='fx_positive_revision'),
        ]


class PortfolioSnapshot(HistoricalModel):
    immutable = True
    owner_lookup = 'portfolio__owner_id'
    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name='snapshots')
    snapshot_date = models.DateField()
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, choices=Currency.choices)
    total_assets = financial_field(validators=[MinValueValidator(0)])
    total_liabilities = financial_field(default=Decimal('0'), validators=[MinValueValidator(0)])
    net_value = financial_field()
    calculation_version = models.CharField(max_length=30)
    inputs = models.JSONField(default=dict, validators=[validate_object],
                             help_text='Valuation/rate IDs, quantities and timestamps used in this calculation.')

    class Meta:
        indexes = [models.Index(fields=['portfolio', '-snapshot_date', '-revision'])]
        constraints = [
            models.UniqueConstraint(fields=['portfolio', 'snapshot_date', 'revision'], name='snapshot_revision_unique'),
            models.CheckConstraint(condition=Q(total_assets__gte=0) & Q(total_liabilities__gte=0),
                                   name='snapshot_nonnegative_totals'),
            models.CheckConstraint(condition=Q(net_value=models.F('total_assets') - models.F('total_liabilities')),
                                   name='snapshot_net_matches_totals'),
            models.CheckConstraint(condition=Q(currency__in=Currency.values), name='snapshot_valid_currency'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='snapshot_positive_revision'),
        ]

    def audit_portfolio(self):
        return self.portfolio

    def clean(self):
        if self.portfolio_id and self.currency != self.portfolio.base_currency:
            raise ValidationError({'currency': 'Snapshot currency must match portfolio base currency.'})


class AuditLog(models.Model):
    owner_lookup = 'portfolio__owner_id'
    portfolio = models.ForeignKey(Portfolio, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                              related_name='portfolio_audit_logs')
    created_at = models.DateTimeField(auto_now_add=True)
    model_label = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=10, choices=[('create', 'Create'), ('update', 'Update')])
    reason = models.TextField(blank=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict)
    objects = ProtectedQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=['portfolio', '-created_at']),
                   models.Index(fields=['model_label', 'object_id'])]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk is not None:
            raise ValidationError('Audit records are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Audit records cannot be deleted.')
