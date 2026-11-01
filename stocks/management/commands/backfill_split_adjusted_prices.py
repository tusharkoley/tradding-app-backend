"""Fetch complete EODHD split history and rebuild persisted split-only OHLC."""
import math
import os
import time
from datetime import date
from decimal import Decimal, InvalidOperation

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from stocks.models import Company, Price, StockSplit, SplitAdjustedPrice
from stocks.management.commands.update_prices_eodhd import build_eodhd_symbol


def parse_splits(payload):
    if not isinstance(payload, list):
        raise ValueError('Expected a JSON array of splits')
    events = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError('Invalid split entry')
        try:
            effective_date = date.fromisoformat(item['date'])
            numerator, denominator = item['split'].split('/')
            numerator, denominator = Decimal(numerator), Decimal(denominator)
            if not all(value.is_finite() and value > 0 for value in (numerator, denominator)):
                raise ValueError('Split terms must be positive and finite')
            ratio = (numerator / denominator).quantize(Decimal('0.000000000001'))
            if ratio <= 0 or ratio >= Decimal('1000000000000'):
                raise ValueError('Split ratio outside supported precision')
        except (KeyError, TypeError, AttributeError, InvalidOperation, ValueError) as exc:
            raise ValueError('Invalid split date or new/old ratio') from exc
        if effective_date in events and events[effective_date] != ratio:
            raise ValueError('Conflicting splits for the same effective date')
        events[effective_date] = ratio
    return events


def adjusted_rows(ticker, events, as_of):
    # Walk backwards: a split affects strictly earlier trading dates only.
    splits = sorted(((day, ratio) for day, ratio in events.items() if day <= as_of), reverse=True)
    index, factor = 0, Decimal(1)
    for raw in Price.objects.filter(ticker=ticker, date__lte=as_of).order_by('-date').iterator(chunk_size=1000):
        while index < len(splits) and splits[index][0] > raw.date:
            factor /= splits[index][1]
            index += 1
        multiplier = float(factor)
        values = {field: getattr(raw, field) * multiplier for field in ('open', 'high', 'low', 'close')}
        if not math.isfinite(multiplier) or multiplier <= 0 or not all(math.isfinite(v) for v in values.values()):
            raise ValueError(f'Non-finite price or adjustment for price {raw.pk}')
        yield SplitAdjustedPrice(price_id=raw.pk, adjustment_factor=multiplier, updated_at=timezone.now(), **values)


class Command(BaseCommand):
    help = 'Fetch complete split history and rebuild adjusted prices from raw EODHD OHLC.'

    def add_arguments(self, parser):
        parser.add_argument('--api-token', help='EODHD API token override')
        parser.add_argument('--tickers', nargs='+', help='Stored tickers; default: all tickers with prices')
        parser.add_argument('--dry-run', action='store_true', help='Fetch and validate without writing')
        parser.add_argument('--local-splits', action='store_true', help='Rebuild using existing StockSplit records without HTTP')
        parser.add_argument('--sleep', type=float, default=0.0)
        parser.add_argument('--default-exchange', default='US')

    def handle(self, *args, **options):
        token = options.get('api_token') or os.environ.get('EODHD_API_KEY')
        if not options['local_splits'] and not token:
            raise CommandError('Set EODHD_API_KEY, or use --local-splits after loading verified split history.')
        if options['sleep'] < 0 or not math.isfinite(options['sleep']):
            raise CommandError('--sleep must be finite and non-negative')
        tickers = Price.objects.order_by('ticker').values_list('ticker', flat=True).distinct()
        if options['tickers']:
            missing = set(options['tickers']) - set(tickers)
            if missing:
                raise CommandError(f'No raw prices for: {", ".join(sorted(missing))}')
            tickers = sorted(set(options['tickers']))
        else:
            tickers = list(tickers)
        countries = dict(Company.objects.filter(ticker__in=tickers).values_list('ticker', 'country'))
        as_of = timezone.localdate()
        base_url = os.environ.get('EODHD_BASE_URL', 'https://eodhd.com/api').rstrip('/')
        for index, ticker in enumerate(tickers, 1):
            self.stdout.write(f'[{index}/{len(tickers)}] Processing split adjustments for {ticker}')
            self.stdout.flush()
            if options['local_splits']:
                events = dict(StockSplit.objects.filter(ticker=ticker).values_list('date', 'ratio'))
            else:
                symbol = build_eodhd_symbol(ticker, countries.get(ticker), options['default_exchange'])
                try:
                    response = requests.get(f'{base_url}/splits/{symbol}', params={'api_token': token, 'fmt': 'json'}, timeout=30)
                except requests.RequestException:
                    # Request exceptions may contain the credential-bearing URL.
                    raise CommandError(f'{ticker}: split-history request failed; no changes made for this ticker.') from None
                if response.status_code != 200:
                    raise CommandError(f'{ticker}: split-history HTTP {response.status_code}; no changes made for this ticker.')
                try:
                    events = parse_splits(response.json())
                except ValueError as exc:
                    raise CommandError(f'{ticker}: {exc}; no changes made for this ticker.') from None
            count = 0
            try:
                with transaction.atomic():
                    if not options['dry_run'] and not options['local_splits']:
                        # Full-history response is authoritative, including corrections/removals.
                        StockSplit.objects.filter(ticker=ticker).delete()
                        StockSplit.objects.bulk_create([StockSplit(ticker=ticker, date=day, ratio=ratio) for day, ratio in events.items()])
                    batch = []
                    for adjusted in adjusted_rows(ticker, events, as_of):
                        count += 1
                        if not options['dry_run']:
                            batch.append(adjusted)
                            if len(batch) == 1000:
                                self.save_batch(batch)
                                batch = []
                    if batch:
                        self.save_batch(batch)
            except (ValueError, ArithmeticError) as exc:
                raise CommandError(f'{ticker}: {exc}; changes rolled back for this ticker.') from None
            verb = 'Would rebuild' if options['dry_run'] else 'Rebuilt'
            self.stdout.write(self.style.SUCCESS(f'{ticker}: {verb} {count} adjusted prices from {len(events)} splits'))
            if not options['local_splits']:
                time.sleep(options['sleep'])

    @staticmethod
    def save_batch(batch):
        SplitAdjustedPrice.objects.bulk_create(
            batch, update_conflicts=True, unique_fields=['price'],
            update_fields=['open', 'high', 'low', 'close', 'adjustment_factor', 'updated_at'],
            batch_size=1000,
        )
