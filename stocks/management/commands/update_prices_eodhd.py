import csv
import json
import os
from datetime import datetime, timedelta, date
from pathlib import Path
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, close_old_connections
from tqdm import tqdm

from stocks.models import Price, Company


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_checkpoint_map(path: Path) -> dict[str, date | None]:
    checkpoint_map: dict[str, date | None] = {}
    if not path.exists():
        return checkpoint_map

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if "|" in line:
            ticker, date_str = line.split("|", 1)
            ticker = ticker.strip()
            date_str = date_str.strip()
            if not ticker:
                continue
            try:
                checkpoint_map[ticker] = parse_date(date_str)
            except Exception:
                # Keep backwards compatibility with older checkpoint files.
                checkpoint_map[ticker] = None
        else:
            # Legacy checkpoint format only stored a ticker.
            checkpoint_map[line] = None

    return checkpoint_map


def append_checkpoint(path: Path, ticker: str, last_date: date) -> None:
    with path.open("a") as cp:
        cp.write(f"{ticker}|{last_date.isoformat()}\n")


KNOWN_EXCHANGE_SUFFIXES = {
    "US",
    "NSE",
    "NS",
    "LSE",
    "L",
    "INDX",
}

SYMBOL_ALIASES = {
    "NIFTY.NSE": "NIFTY50.INDX",
    "NIFTY.NS": "NIFTY50.INDX",
}

SKIP_TICKERS = {
    "NIFTY.NSE",
    "NIFTY.NS",
}


def build_eodhd_symbol(ticker, country, default_exchange):
    raw_ticker = (ticker or "").strip()
    upper_ticker = raw_ticker.upper()

    if upper_ticker in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[upper_ticker]

    # Normalize common UK suffix from .L (Yahoo style) to .LSE (EODHD style)
    if upper_ticker.endswith(".L"):
        return f"{raw_ticker[:-2]}.LSE"

    # Normalize NSE shorthand suffix used in some sources.
    if upper_ticker.endswith(".NS"):
        return f"{raw_ticker[:-3]}.NSE"

    if "." in raw_ticker:
        parts = raw_ticker.split(".")
        trailing = parts[-1].upper()

        # If the last segment is a known exchange suffix, normalize class separators in base.
        if trailing in KNOWN_EXCHANGE_SUFFIXES:
            exchange = "LSE" if trailing == "L" else "NSE" if trailing == "NS" else trailing
            base = "-".join(parts[:-1])
            return f"{base}.{exchange}"

        # Otherwise treat dot as a class separator (e.g. BF.B -> BF-B) and append inferred exchange.
        class_base = "-".join(parts)
    else:
        class_base = raw_ticker

    country_value = (country or "").strip().lower()
    if country_value in {"uk", "united kingdom", "great britain", "england"}:
        inferred_exchange = "LSE"
    elif country_value in {"india"}:
        inferred_exchange = "NSE"
    elif country_value in {"usa", "united states", "us", "u.s."}:
        inferred_exchange = "US"
    else:
        inferred_exchange = default_exchange

    return f"{class_base}.{inferred_exchange}"


class Command(BaseCommand):
    help = "Incrementally load EOD price data from EODHD for a list of tickers"

    def add_arguments(self, parser):
        parser.add_argument("--api-token", required=False, help="EODHD API token (overrides EODHD_API_KEY env variable)")
        parser.add_argument("--default-exchange", default="US", help="Default exchange suffix to append (e.g. US or NS). If ticker already contains a dot, it is used as-is.")
        parser.add_argument("--dry-run", action="store_true", help="Do not write to DB; just report what would be done")
        parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between API calls to avoid rate limits")
        parser.add_argument("--years", type=int, default=20, help="Number of years to fetch when no data exists (default 20)")
        parser.add_argument("--start-date", default=None, help="Optional start date (YYYY-MM-DD) to force fetching from an earlier date (useful to backfill historical data)")
        parser.add_argument("--checkpoint-file", default="/tmp/eodhd_checkpoint.txt", help="File to track completed tickers so a restart resumes from where it left off (default: /tmp/eodhd_checkpoint.txt)")
        parser.add_argument("--tickers", nargs="+", default=None, help="Only process these specific tickers (e.g. --tickers SPY.US AAPL.US)")
        parser.add_argument("--http-timeout", type=float, default=30.0, help="HTTP timeout in seconds per API call (default 30)")
        parser.add_argument("--http-retries", type=int, default=2, help="Number of retries for failed HTTP calls (default 2)")
        parser.add_argument("--http-backoff", type=float, default=1.5, help="Backoff multiplier (seconds) between HTTP retries (default 1.5)")

    def handle(self, *args, **options):
        api_token = options.get("api_token") or os.environ.get("EODHD_API_KEY")
        default_exchange = options["default_exchange"]
        dry_run = options["dry_run"]
        sleep_secs = options.get("sleep", 0.0)
        http_timeout = float(options.get("http_timeout") or 30.0)
        http_retries = int(options.get("http_retries") or 2)
        http_backoff = float(options.get("http_backoff") or 1.5)

        if not api_token:
            raise CommandError("EODHD API token not provided. Set EODHD_API_KEY in environment or pass --api-token")

        checkpoint_path = Path(options["checkpoint_file"])

        # Load the last successfully processed date for each ticker from checkpoint file.
        # Format: TICKER|YYYY-MM-DD
        checkpoint_map = load_checkpoint_map(checkpoint_path)
        if checkpoint_map:
            self.stdout.write(self.style.WARNING(
                f"Resuming — loaded checkpoint state for {len(checkpoint_map)} tickers from {checkpoint_path}"
            ))

        # Read tickers from Company table (or use the --tickers override)
        filter_tickers = options.get("tickers")
        if filter_tickers:
            companies = list(
                Company.objects.filter(ticker__in=filter_tickers).values("id", "ticker", "country")
            )
            found_tickers = {row["ticker"] for row in companies}
            missing = set(filter_tickers) - found_tickers
            if missing:
                self.stdout.write(self.style.WARNING(f"Tickers not found in Company table (skipping): {', '.join(sorted(missing))}"))
        else:
            companies = list(Company.objects.values("id", "ticker", "country"))

        tickers = [row["ticker"] for row in companies]
        company_id_by_ticker = {row["ticker"]: row["id"] for row in companies}
        country_by_ticker = {row["ticker"]: row.get("country") for row in companies}

        if not tickers:
            self.stdout.write(self.style.WARNING("No matching companies found in Company table."))
            return

        session = requests.Session()
        session.params = {"api_token": api_token, "fmt": "json"}

        years = int(options.get("years") or 20)
        start_date_opt = options.get("start_date")
        if start_date_opt:
            try:
                sd = parse_date(start_date_opt)
            except Exception:
                raise CommandError("Invalid --start-date format, expected YYYY-MM-DD")
        else:
            sd = None

        new_rows_total = 0
        no_data_count = 0
        fetch_failed_count = 0
        db_failed_count = 0
        skipped_count = 0
        unexpected_error_count = 0
        interrupted = False
        try:
            for ticker in tqdm(tickers, desc="Processing tickers"):
                try:
                    if ticker.upper() in SKIP_TICKERS:
                        skipped_count += 1
                        self.stdout.write(self.style.WARNING(f"Skipping unsupported benchmark ticker: {ticker}"))
                        if not dry_run:
                            append_checkpoint(checkpoint_path, ticker, date.today())
                        continue

                    symbol = build_eodhd_symbol(
                        ticker=ticker,
                        country=country_by_ticker.get(ticker),
                        default_exchange=default_exchange,
                    )

                    # Determine latest date we already have for this ticker
                    latest = Price.objects.filter(ticker=ticker).order_by("-date").values_list("date", flat=True).first()
                    earliest = Price.objects.filter(ticker=ticker).order_by("date").values_list("date", flat=True).first()
                    checkpoint_date = checkpoint_map.get(ticker)

                    # Determine from_date:
                    # - If --start-date provided, backfill from that date (we will insert bars earlier than existing earliest)
                    # - Else if checkpoint exists, fetch from the day after the last successful checkpoint date
                    # - Else if we have latest data, fetch from latest+1 (incremental forward)
                    # - Else fetch `years` years back from today
                    if sd:
                        from_date = sd
                    elif checkpoint_date:
                        from_date = checkpoint_date + timedelta(days=1)
                    elif latest:
                        from_date = latest + timedelta(days=1)
                    else:
                        from_date = date.today() - timedelta(days=years * 365)

                    to_date = date.today()

                    # EODHD endpoint: /api/eod/{symbol}?from=YYYY-MM-DD&to=YYYY-MM-DD&api_token=...
                    url = f"https://eodhd.com/api/eod/{symbol}"
                    params = {"from": from_date.isoformat(), "to": to_date.isoformat(), "api_token": api_token, "fmt": "json"}

                    r = None
                    last_http_error = None
                    for attempt in range(http_retries + 1):
                        try:
                            r = session.get(url, params=params, timeout=http_timeout)
                            if r.status_code == 200:
                                break
                            last_http_error = f"HTTP {r.status_code} - {r.text[:200]}"
                        except Exception as e:
                            last_http_error = str(e)

                        if attempt < http_retries:
                            time.sleep(http_backoff * (attempt + 1))

                    if r is None or r.status_code != 200:
                        fetch_failed_count += 1
                        self.stdout.write(self.style.ERROR(f"Failed to fetch {symbol}: {last_http_error}"))
                        continue

                    if sleep_secs:
                        time.sleep(sleep_secs)

                    try:
                        data = r.json()
                    except json.JSONDecodeError:
                        fetch_failed_count += 1
                        self.stdout.write(self.style.ERROR(f"Invalid JSON for {symbol}. Response start: {r.text[:200]}"))
                        continue

                    if not data:
                        no_data_count += 1
                        self.stdout.write(self.style.NOTICE(f"No new data for {symbol} (from {from_date} to {to_date})"))
                        continue

                    # Reconnect after the (potentially slow) API call before hitting the DB
                    close_old_connections()

                    # Resolve company FK once per ticker, not once per bar
                    comp_id = company_id_by_ticker.get(ticker)

                    # Data is expected to be a list of daily bars. Create Price objects for dates > latest
                    price_objs = []
                    for bar in data:
                        # Expected keys: date, open, high, low, close, volume, adjusted_close, split, dividend
                        try:
                            bar_date = parse_date(bar.get("date"))
                        except Exception:
                            continue

                        # Determine whether to include this bar:
                        if sd:
                            # backfill mode: include bars between sd..to_date that are earlier than existing earliest
                            if earliest and bar_date >= earliest:
                                # this bar is at/after earliest existing row -> skip to avoid duplicate/overlap
                                continue
                            if bar_date < sd:
                                continue
                        else:
                            # incremental forward mode: skip bars up to and including latest
                            if latest and bar_date <= latest:
                                continue

                        price = Price(
                            ticker=ticker,
                            date=bar_date,
                            open=bar.get("open") or bar.get("o") or 0.0,
                            high=bar.get("high") or bar.get("h") or 0.0,
                            low=bar.get("low") or bar.get("l") or 0.0,
                            close=bar.get("close") or bar.get("c") or 0.0,
                            volume=int(bar.get("volume") or bar.get("v") or 0),
                            stock_splits=float(bar.get("split") or 0.0),
                            dividends=float(bar.get("dividend") or 0.0),
                        )

                        if comp_id:
                            price.company_id = comp_id

                        price_objs.append(price)

                    if not price_objs:
                        no_data_count += 1
                        self.stdout.write(self.style.NOTICE(f"No incremental rows to add for {ticker}"))
                        continue

                    self.stdout.write(f"Preparing to insert {len(price_objs)} rows for {ticker} ({symbol})")

                    if dry_run:
                        new_rows_total += len(price_objs)
                        continue

                    # Bulk create, ignoring conflicts (rows that already exist because of race conditions)
                    for attempt in range(3):
                        try:
                            with transaction.atomic():
                                Price.objects.bulk_create(price_objs, ignore_conflicts=True)
                            new_rows_total += len(price_objs)
                            last_inserted_date = max(price_obj.date for price_obj in price_objs)
                            self.stdout.write(self.style.SUCCESS(f"Inserted {len(price_objs)} rows for {ticker}"))
                            append_checkpoint(checkpoint_path, ticker, last_inserted_date)
                            checkpoint_map[ticker] = last_inserted_date
                            break
                        except Exception as e:
                            self.stdout.write(self.style.WARNING(f"DB error for {ticker} (attempt {attempt + 1}/3): {e}"))
                            if attempt < 2:
                                time.sleep(3)
                                close_old_connections()  # force a fresh connection before retry
                            else:
                                db_failed_count += 1
                                self.stdout.write(self.style.ERROR(f"Giving up on {ticker} after 3 attempts"))
                except Exception as e:
                    unexpected_error_count += 1
                    self.stdout.write(self.style.ERROR(f"Unexpected error while processing {ticker}: {e}"))
                    continue
        except KeyboardInterrupt:
            interrupted = True
            self.stdout.write(self.style.WARNING("Interrupted by user. Exiting gracefully with summary."))
        self.stdout.write(self.style.SUCCESS(f"Done. New rows added (counted): {new_rows_total}"))
        self.stdout.write(
            "Run summary: "
            f"skipped={skipped_count}, "
            f"no_data={no_data_count}, "
            f"fetch_failed={fetch_failed_count}, "
            f"db_failed={db_failed_count}, "
            f"unexpected_errors={unexpected_error_count}"
        )
        if interrupted:
            self.stdout.write(self.style.WARNING("Run ended early due to interrupt."))
