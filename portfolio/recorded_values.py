"""Read persisted valuations only; no market pricing or currency conversion."""
from decimal import Decimal, ROUND_HALF_EVEN, localcontext


def recorded_value(asset):
    valuation = next(iter(asset.recorded_valuations), None)
    if valuation is None:
        return {'recorded_value': None, 'valuation_status': 'unvalued', 'valuation_date': None}
    entries = asset.holding_entries
    with localcontext() as context:
        context.prec = 80
        quantity = sum((entry.quantity for entry in entries), Decimal('0'))
        recorded_quantity = sum((entry.quantity for entry in entries
                                 if entry.created_at <= valuation.created_at
                                 and entry.transaction_date <= valuation.valued_at.date()), Decimal('0'))
        review = valuation.basis == 'total' and quantity != recorded_quantity
        value = valuation.value * quantity if valuation.basis == 'unit' else valuation.value
        value = value.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_EVEN)
    return {'recorded_value': None if review else format(value, '.8f'),
            'valuation_status': 'needs_review' if review else 'recorded',
            'valuation_date': valuation.valued_at.isoformat()}
