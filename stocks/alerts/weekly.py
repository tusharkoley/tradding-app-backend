from collections import defaultdict
from datetime import timedelta
import math

from stocks.models import Company, Price

COUNTRIES = {
    'US': {'us', 'usa', 'united states', 'united states of america', 'u.s.'},
    'UK': {'uk', 'united kingdom', 'great britain', 'england'},
    'India': {'india', 'in'},
}


class DataNotReady(ValueError):
    pass


def rank_market(companies, prices, friday, limit=10):
    """Use exact Friday endpoint and latest prior-week close per ticker."""
    baseline = friday - timedelta(days=7)
    by_ticker = defaultdict(list)
    for row in prices:
        by_ticker[row['ticker']].append(row)
    ranked = []
    for ticker, name in companies.items():
        rows = by_ticker[ticker]
        starts = [r for r in rows if baseline - timedelta(days=4) <= r['date'] <= baseline]
        ends = [r for r in rows if r['date'] == friday]
        if not starts or not ends:
            continue
        start, end = max(starts, key=lambda r: r['date']), ends[0]
        a, b = start['split_adjusted_price__close'], end['split_adjusted_price__close']
        if a is None or b is None or not math.isfinite(a) or not math.isfinite(b) or a <= 0 or b <= 0:
            continue
        ranked.append({'ticker': ticker, 'name': name, 'return': (b / a - 1) * 100,
                       'start': a, 'end': b, 'start_date': start['date'], 'end_date': end['date']})
    ranked.sort(key=lambda row: (-row['return'], row['ticker']))
    return ranked[:limit], len(ranked)


def build_report(friday, options):
    markets = options.get('markets', ['US', 'UK', 'India'])
    limit = options.get('top_n', 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('top_n must be between 1 and 100')
    companies = list(Company.objects.values('ticker', 'company_name', 'country'))
    groups = {market: {} for market in markets}
    if not groups or any(market not in COUNTRIES for market in markets):
        raise ValueError('Markets must be US, UK or India')
    ticker_markets = defaultdict(set)
    for company in companies:
        for market in markets:
            if company['country'].strip().lower() in COUNTRIES[market]:
                groups[market][company['ticker']] = company['company_name']
                ticker_markets[company['ticker']].add(market)
    if any(len(value) > 1 for value in ticker_markets.values()):
        raise ValueError('Ambiguous ticker shared across countries')
    tickers = list(ticker_markets)
    prices = list(Price.objects.filter(
        ticker__in=tickers, date__gte=friday - timedelta(days=11), date__lte=friday,
    ).values('ticker', 'date', 'split_adjusted_price__close'))
    lines = [f'Weekly top {limit} tracked stocks — week ending {friday}', '',
             'Ranked by split-adjusted closing-price return (excluding dividends).',
             'Universe: companies stored in Tradezens, not every listed stock.', '']
    for market in markets:
        universe = groups[market]
        leaders, eligible = rank_market(universe, prices, friday, limit)
        if not eligible:
            raise DataNotReady(f'{market}: no eligible adjusted Friday prices')
        lines.extend([market, f'Eligible: {eligible}/{len(universe)}; excluded: {len(universe) - eligible}',
                      'Rank | Ticker | Company | Weekly return | Start close/date | End close/date'])
        for index, row in enumerate(leaders, 1):
            lines.append(f"{index} | {row['ticker']} | {row['name']} | {row['return']:+.2f}% | "
                         f"{row['start']:.4f} ({row['start_date']}) | {row['end']:.4f} ({row['end_date']})")
        lines.append('')
    return f'Weekly stock performers — {friday}', '\n'.join(lines)
