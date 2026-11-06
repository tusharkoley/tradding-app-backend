"""Owner-scoped writes and locked ledger operations; no market/provider calls."""
from django.core.exceptions import ValidationError
from django.db import transaction

from django.db.models import Max
from django.utils import timezone

from .models import Asset, AssetCategory, AssetTransaction, AssetValuation, Portfolio


def locked_asset(*, user, asset_id):
    # Consistent lock order: portfolio -> asset -> transaction. Foreign IDs look missing.
    asset = Asset.objects.for_user(user).get(pk=asset_id)
    Portfolio.objects.for_user(user).select_for_update().get(pk=asset.portfolio_id)
    return (Asset.objects.for_user(user).select_for_update(of=('self',))
            .select_related('portfolio', 'category').get(pk=asset_id))


@transaction.atomic
def create_portfolio(*, user, data):
    portfolio = Portfolio(owner=user, **data)
    portfolio.save(actor=user, reason='API portfolio creation')
    return portfolio


@transaction.atomic
def update_portfolio(*, user, portfolio_id, data):
    portfolio = Portfolio.objects.for_user(user).select_for_update().get(pk=portfolio_id)
    for field, value in data.items():
        setattr(portfolio, field, value)
    portfolio.save(actor=user, reason='API portfolio update')
    return portfolio


@transaction.atomic
def create_asset(*, user, data):
    payload = dict(data)
    opening = payload.pop('opening_transaction', None)
    initial_valuation = payload.pop('initial_valuation', None)
    portfolio = Portfolio.objects.for_user(user).select_for_update().get(pk=payload['portfolio'].pk)
    if portfolio.is_archived:
        raise ValidationError({'portfolio': 'Cannot add assets to an archived portfolio.'})
    payload['portfolio'] = portfolio
    payload['category'] = AssetCategory.objects.select_for_update().get(pk=payload['category'].pk)
    asset = Asset(**payload)
    asset.save(actor=user, reason='API asset creation')
    if opening:
        record_transaction(user=user, asset_id=asset.pk, data=opening)
    if initial_valuation:
        record_valuation(user=user, asset_id=asset.pk, data=initial_valuation)
    return asset


@transaction.atomic
def record_valuation(*, user, asset_id, data):
    asset = locked_asset(user=user, asset_id=asset_id)
    if asset.is_archived or asset.portfolio.is_archived:
        raise ValidationError('Cannot add valuations to an archived asset or portfolio.')
    if data['valued_at'] > timezone.now():
        raise ValidationError({'valued_at': 'A recorded valuation cannot be in the future.'})
    revision = (asset.valuations.filter(valued_at=data['valued_at']).aggregate(value=Max('revision'))['value'] or 0) + 1
    valuation = AssetValuation(asset=asset, currency=asset.currency, method='manual', revision=revision, **data)
    valuation.save(actor=user, reason='User-recorded manual valuation')
    return valuation


@transaction.atomic
def update_asset(*, user, asset_id, data):
    asset = locked_asset(user=user, asset_id=asset_id)
    for field, value in data.items():
        setattr(asset, field, value)
    asset.save(actor=user, reason='API asset update')
    return asset


@transaction.atomic
def archive_asset(*, user, asset_id):
    asset = locked_asset(user=user, asset_id=asset_id)
    if not asset.is_archived:
        asset.is_archived = True
        asset.save(actor=user, reason='API asset archival')
    return asset


@transaction.atomic
def record_transaction(*, user, asset_id, data):
    asset = locked_asset(user=user, asset_id=asset_id)
    allowed = {'kind', 'transaction_date', 'quantity', 'unit_price', 'amount', 'fee', 'currency', 'notes'}
    if set(data) - allowed:
        raise ValidationError('Unsupported transaction fields.')
    entry = AssetTransaction(asset=asset, **{'currency': asset.currency, **data})
    entry.save(actor=user, reason='Recorded transaction')
    return entry


def locked_entry(*, user, entry_id):
    original = AssetTransaction.objects.for_user(user).get(pk=entry_id)
    locked_asset(user=user, asset_id=original.asset_id)
    return AssetTransaction.objects.for_user(user).select_for_update().get(pk=entry_id)


def reverse_locked(*, user, original, reason, transaction_date=None):
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reversal reason is required.'})
    if original.reversal_of_id or AssetTransaction.objects.filter(reversal_of=original).exists():
        raise ValidationError('Only an unreversed original transaction can be reversed.')
    reversal = AssetTransaction(
        asset=original.asset, kind=original.kind,
        transaction_date=transaction_date or original.transaction_date,
        quantity=-original.quantity, unit_price=original.unit_price,
        amount=-original.amount, fee=-original.fee, currency=original.currency,
        notes=reason, reversal_of=original,
    )
    # Only internal batch operations defer validation. Their caller validates before commit.
    reversal.save(actor=user, reason=reason, _validate_holdings=False)
    return reversal


@transaction.atomic
def reverse_transaction(*, user, entry_id, reason, transaction_date=None):
    original = locked_entry(user=user, entry_id=entry_id)
    reversal = reverse_locked(user=user, original=original, reason=reason, transaction_date=transaction_date)
    original.asset.validate_holdings()
    return reversal


@transaction.atomic
def correct_transaction(*, user, entry_id, reason, changes):
    allowed = {'kind', 'transaction_date', 'quantity', 'unit_price', 'amount', 'fee', 'notes'}
    if not changes or set(changes) - allowed:
        raise ValidationError('Provide only editable transaction fields.')
    original = locked_entry(user=user, entry_id=entry_id)
    reversal = reverse_locked(user=user, original=original, reason=reason)
    payload = {field: getattr(original, field) for field in allowed}
    payload.update(changes)
    replacement = AssetTransaction(asset=original.asset, currency=original.currency,
                                   correction_of=original, **payload)
    replacement.save(actor=user, reason=f'Correction of transaction {original.pk}: {reason}',
                     _validate_holdings=False)
    original.asset.validate_holdings()
    return reversal, replacement
