"""Fill missing company profiles using Yahoo Finance; never change stored tickers."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stocks.models import Company
from .load_companies import _normalize_industry


FIELDS = ("company_name", "description", "soctor", "industry", "industry_subgroup", "website", "address", "country")


def missing(value):
    return value is None or str(value).strip().lower() in {"", "unknown", "n/a", "na", "none", "null", "nan", "-"}


def yahoo_symbol(ticker):
    symbol = ticker.strip().upper()
    if symbol in {"NIFTY.NSE", "NIFTY.NS"}:
        return "^NSEI"
    for suffix, replacement in ((".US", ""), (".NSE", ".NS"), (".LSE", ".L")):
        if symbol.endswith(suffix):
            base = symbol[:-len(suffix)]
            return base.replace(".", "-") + replacement if suffix == ".US" else base + replacement
    return symbol.replace(".", "-") if symbol in {"BRK.B", "BRK.A", "BF.B"} else symbol


def profile_values(info):
    group, subgroup = _normalize_industry(info.get("sector"), info.get("industry"))
    address = ", ".join(str(info[k]).strip() for k in
                        ("address1", "address2", "city", "state", "zip", "country")
                        if not missing(info.get(k)))
    country = info.get("country")
    country = {"United States": "USA", "United Kingdom": "UK"}.get(country, country)
    return {
        "company_name": info.get("longName") or info.get("shortName"),
        "description": info.get("longBusinessSummary"),
        "soctor": info.get("sector"),
        # The app uses broad sectors as industry, with Yahoo industry as subgroup.
        "industry": group,
        "industry_subgroup": subgroup,
        "website": info.get("website"), "address": address, "country": country,
    }


class Command(BaseCommand):
    help = "Fill missing company profile fields from Yahoo Finance (existing values are preserved)."

    def add_arguments(self, parser):
        parser.add_argument("--tickers", nargs="+", help="Stored database tickers, e.g. AAPL.US RELIANCE.NSE")
        parser.add_argument("--limit", type=int, help="Maximum number of incomplete companies to fetch")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between requests")
        parser.add_argument("--retries", type=int, default=2)
        parser.add_argument("--audit-file", default=None, help="JSONL log including original field values")

    def handle(self, *args, **options):
        if options["sleep"] < 0 or options["retries"] < 0 or (options["limit"] is not None and options["limit"] < 1):
            raise CommandError("Sleep/retries must be nonnegative and limit must be positive.")
        companies = Company.objects.order_by("pk")
        if options["tickers"]:
            companies = companies.filter(ticker__in=options["tickers"])
        # Snapshot only incomplete rows, avoiding an open cursor during slow HTTP calls.
        candidates = [c for c in companies if any(missing(getattr(c, f)) for f in FIELDS)]
        if options["limit"]:
            candidates = candidates[:options["limit"]]
        audit_path = Path(options["audit_file"] or
                          f"/tmp/tradezen-company-enrichment-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.jsonl")
        self.stdout.write(f"Incomplete companies: {len(candidates)}; dry run: {options['dry_run']}; audit: {audit_path}")
        updated = failed = unchanged = consecutive_failures = 0
        with audit_path.open("x", encoding="utf-8") as audit:
            for company in candidates:
                symbol = yahoo_symbol(company.ticker)
                try:
                    for attempt in range(options["retries"] + 1):
                        try:
                            info = yf.Ticker(symbol).get_info()
                            if not isinstance(info, dict) or not any(info.get(k) for k in ("longName", "shortName", "longBusinessSummary")):
                                raise ValueError("Yahoo returned no company profile")
                            break
                        except Exception:
                            if attempt == options["retries"]:
                                raise
                            time.sleep(2 ** (attempt + 1))
                    values = profile_values(info)
                    # Re-read inside a short transaction to preserve concurrent edits.
                    with transaction.atomic():
                        current = Company.objects.select_for_update().get(pk=company.pk)
                        changes = {}
                        for field, value in values.items():
                            if missing(getattr(current, field)) and not missing(value):
                                length = Company._meta.get_field(field).max_length
                                changes[field] = {"before": getattr(current, field), "after": str(value).strip()[:length]}
                        entry = {"id": current.pk, "ticker": current.ticker, "yahoo_symbol": symbol,
                                 "dry_run": options["dry_run"], "changes": changes}
                        # Write originals before performing any database mutation.
                        audit.write(json.dumps(entry) + "\n")
                        audit.flush()
                        if changes and not options["dry_run"]:
                            for field, change in changes.items():
                                setattr(current, field, change["after"])
                            current.save(update_fields=list(changes))
                    if changes:
                        updated += 1
                        if not options["dry_run"]:
                            cache.delete("stocks:companies:v1")
                            cache.delete("industry_perf:countries:v1")
                    else:
                        unchanged += 1
                    consecutive_failures = 0
                    self.stdout.write(f"{company.ticker} -> {symbol}: {', '.join(changes) or 'no missing fields available'}")
                except Exception as exc:
                    failed += 1
                    consecutive_failures += 1
                    audit.write(json.dumps({"id": company.pk, "ticker": company.ticker, "error": str(exc)}) + "\n")
                    audit.flush()
                    self.stderr.write(f"{company.ticker}: {exc}")
                    if consecutive_failures >= 5:
                        raise CommandError("Stopped after five consecutive failures; inspect audit log before retrying.") from exc
                time.sleep(options["sleep"])
        self.stdout.write(self.style.SUCCESS(
            f"{'Would update' if options['dry_run'] else 'Updated'}: {updated}; unchanged: {unchanged}; failed: {failed}. Audit: {audit_path}"
        ))
