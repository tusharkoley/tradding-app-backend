import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stocks.models import Company


INDUSTRY_ALIASES = {
    "financial services": "Financials",
    "financial": "Financials",
    "financials": "Financials",
    "communication service": "Communication Services",
    "communication services": "Communication Services",
    "telecom services": "Communication Services",
    "telecommunications": "Communication Services",
    "consumer cyclical": "Consumer Cyclical",
    "consumer defensive": "Consumer Defensive",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "information technology": "Technology",
    "technology": "Technology",
    "industrials": "Industrials",
    "utilities": "Utilities",
    "energy": "Energy",
    "materials": "Materials",
    "real estate": "Real Estate",
}


def _clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_industry(sector, industry):
    subgroup = _clean_text(industry) or _clean_text(sector) or "Unknown"
    sector_clean = _clean_text(sector)
    group_source = sector_clean or subgroup
    group = re.split(r"\s*[-/|>]\s*", group_source, maxsplit=1)[0]
    group = _clean_text(group) or "Unknown"
    normalized_group = INDUSTRY_ALIASES.get(group.lower(), group)
    return normalized_group, subgroup


class Command(BaseCommand):
    help = "Load company lists for India (NIFTY), USA (S&P 500), and UK (FTSE 100) into the Company table"

    def add_arguments(self, parser):
        parser.add_argument(
            "--nifty-file",
            default="data/ind_nifty500list.csv",
            help="Path to NIFTY500 CSV (default: data/ind_nifty500list.csv)",
        )
        parser.add_argument(
            "--snp-file",
            default="data/sp500_companies.csv",
            help="Path to S&P 500 CSV (default: data/sp500_companies.csv)",
        )
        parser.add_argument(
            "--ftse-file",
            default="data/FTSE_100_Companies.csv",
            help="Path to FTSE 100 CSV (default: data/FTSE_100_Companies.csv)",
        )
        parser.add_argument(
            "--only-snp",
            action="store_true",
            help="Load only S&P 500 data and skip NIFTY",
        )
        parser.add_argument(
            "--only-nifty",
            action="store_true",
            help="Load only NIFTY data and skip S&P 500",
        )
        parser.add_argument(
            "--only-ftse",
            action="store_true",
            help="Load only FTSE 100 data and skip NIFTY/S&P 500",
        )

    def handle(self, *args, **options):
        base_dir = Path(__file__).resolve().parent / "data"
        only_snp = options.get("only_snp", False)
        only_nifty = options.get("only_nifty", False)
        only_ftse = options.get("only_ftse", False)

        enabled_only_flags = [only_snp, only_nifty, only_ftse]
        if sum(1 for flag in enabled_only_flags if flag) > 1:
            raise CommandError("Use only one of --only-snp, --only-nifty, or --only-ftse")

        nifty_path = Path(options["nifty_file"])
        snp_path = Path(options["snp_file"])
        ftse_path = Path(options["ftse_file"])

        # If relative paths provided, resolve relative to data dir
        if not nifty_path.exists():
            alt = base_dir / nifty_path.name
            if alt.exists():
                nifty_path = alt

        if not snp_path.exists():
            alt = base_dir / snp_path.name
            if alt.exists():
                snp_path = alt

        if not ftse_path.exists():
            alt = base_dir / ftse_path.name
            if alt.exists():
                ftse_path = alt

        if not nifty_path.exists() and not snp_path.exists() and not ftse_path.exists():
            raise CommandError(
                f"None of the files were found: nifty ({nifty_path}), snp ({snp_path}), ftse ({ftse_path})"
            )

        if not only_snp and not only_ftse and nifty_path.exists():
            self.stdout.write(f"Loading NIFTY200 companies from {nifty_path}")
            self.load_nifty(nifty_path)
        elif not only_snp and not only_ftse:
            self.stdout.write(self.style.WARNING(f"NIFTY file not found: {nifty_path} - skipping"))

        if not only_nifty and not only_ftse and snp_path.exists():
            self.stdout.write(f"Loading S&P 500 companies from {snp_path}")
            self.load_snp(snp_path)
        elif not only_nifty and not only_ftse:
            self.stdout.write(self.style.WARNING(f"S&P file not found: {snp_path} - skipping"))

        if not only_nifty and not only_snp and ftse_path.exists():
            self.stdout.write(f"Loading FTSE 100 companies from {ftse_path}")
            self.load_ftse(ftse_path)
        elif not only_nifty and not only_snp:
            self.stdout.write(self.style.WARNING(f"FTSE file not found: {ftse_path} - skipping"))

        self.ensure_benchmarks()
        self.stdout.write(self.style.SUCCESS("Company load complete"))

    def ensure_benchmarks(self):
        """Make sure benchmark tickers (SPY.US, NIFTY.NS) exist in the Company table."""
        benchmarks = [
            dict(ticker="SPY.US",   company_name="SPDR S&P 500 ETF Trust", industry="ETF", country="USA"),
            dict(ticker="NIFTY.NSE", company_name="Nifty 50 Index",         industry="Index", country="India"),
        ]
        for b in benchmarks:
            ticker = b.pop("ticker")
            normalized_industry, subgroup = _normalize_industry("", b.get("industry", ""))
            defaults = {
                **b,
                "soctor": "",
                "industry": normalized_industry,
                "industry_subgroup": subgroup,
                "description": "",
                "website": None,
                "address": "",
            }
            _, created = Company.objects.get_or_create(ticker=ticker, defaults=defaults)
            status = "Added" if created else "Already exists"
            self.stdout.write(f"Benchmark {ticker}: {status}")

    def load_nifty(self, path: Path):
        # NIFTY CSV columns: Company Name,Industry,Symbol,Series,ISIN Code
        # Load all existing tickers once to avoid N+1 queries over the network
        existing_map = {c.ticker.upper(): c for c in Company.objects.only("id", "ticker")}

        to_create = []
        to_update = []

        with path.open() as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw_ticker = row.get("Symbol", "").strip()
                if not raw_ticker:
                    continue
                name = row.get("Company Name", "").strip()
                industry = row.get("Industry", "").strip()
                normalized_industry, subgroup = _normalize_industry("", industry)

                base = raw_ticker.split('.')[0]
                ticker = raw_ticker if '.' in raw_ticker else f"{base}.NSE"

                candidates = [ticker.upper(), raw_ticker.upper(), base.upper(),
                               f"{base}.NSE".upper(), f"{base}.US".upper()]
                existing = next((existing_map[c] for c in candidates if c in existing_map), None)

                data = dict(
                    company_name=name,
                    soctor="",
                    industry=normalized_industry,
                    industry_subgroup=subgroup,
                    description="",
                    country="India",
                    website=None,
                    address="",
                )

                if existing:
                    for k, v in data.items():
                        setattr(existing, k, v)
                    to_update.append(existing)
                else:
                    to_create.append(Company(ticker=ticker, **data))
                    existing_map[ticker.upper()] = to_create[-1]

        update_fields = ["company_name", "soctor", "industry", "industry_subgroup", "description", "country", "website", "address"]
        with transaction.atomic():
            if to_create:
                Company.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                Company.objects.bulk_update(to_update, update_fields)

        self.stdout.write(self.style.SUCCESS(f"NIFTY: Added {len(to_create)}, updated {len(to_update)} companies"))

    def load_snp(self, path: Path):
        # Supports multiple S&P CSV formats, including:
        # 1) Symbol,Name,Sector
        # 2) Exchange,Symbol,Shortname,Longname,Sector,Industry,...,Country,...,Longbusinesssummary
        # Load all existing tickers once to avoid N+1 queries over the network
        existing_map = {c.ticker.upper(): c for c in Company.objects.only("id", "ticker")}

        to_create = []
        to_update = []

        with path.open() as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw_ticker = row.get("Symbol", "").strip()
                if not raw_ticker:
                    continue

                name = (
                    row.get("Longname", "").strip()
                    or row.get("Name", "").strip()
                    or row.get("Shortname", "").strip()
                )
                sector = row.get("Sector", "").strip()
                industry = row.get("Industry", "").strip() or sector
                normalized_industry, subgroup = _normalize_industry(sector, industry)
                description = row.get("Longbusinesssummary", "").strip()[:2000]
                country_raw = row.get("Country", "").strip()

                country_map = {
                    "united states": "USA",
                    "us": "USA",
                    "u.s.": "USA",
                }
                country = country_map.get(country_raw.lower(), country_raw or "USA")

                city = row.get("City", "").strip()
                state = row.get("State", "").strip()
                if city and state:
                    address = f"{city}, {state}"
                else:
                    address = city or state or ""

                base = raw_ticker.split('.')[0]
                ticker = raw_ticker if '.' in raw_ticker else f"{base}.US"

                candidates = [ticker.upper(), raw_ticker.upper(), base.upper(),
                               f"{base}.NSE".upper(), f"{base}.US".upper()]
                existing = next((existing_map[c] for c in candidates if c in existing_map), None)

                data = dict(
                    company_name=name,
                    soctor=sector,
                    industry=normalized_industry,
                    industry_subgroup=subgroup,
                    description=description,
                    country=country,
                    website=None,
                    address=address,
                )

                if existing:
                    for k, v in data.items():
                        # Keep existing non-empty values when incoming CSV field is empty.
                        if v in (None, ""):
                            continue
                        setattr(existing, k, v)
                    to_update.append(existing)
                else:
                    to_create.append(Company(ticker=ticker, **data))
                    existing_map[ticker.upper()] = to_create[-1]

        update_fields = ["company_name", "soctor", "industry", "industry_subgroup", "description", "country", "website", "address"]
        with transaction.atomic():
            if to_create:
                Company.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                Company.objects.bulk_update(to_update, update_fields)

        self.stdout.write(self.style.SUCCESS(f"S&P: Added {len(to_create)}, updated {len(to_update)} companies"))

    def load_ftse(self, path: Path):
        # FTSE CSV columns: Company Name,Ticker,Sector,Industry,...
        existing_map = {c.ticker.upper(): c for c in Company.objects.only("id", "ticker")}

        to_create = []
        to_update = []

        with path.open() as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw_ticker = row.get("Ticker", "").strip()
                if not raw_ticker:
                    continue

                company_name = row.get("Company Name", "").strip()
                sector = row.get("Sector", "").strip()
                industry = row.get("Industry", "").strip() or sector
                normalized_industry, subgroup = _normalize_industry(sector, industry)

                # Normalize common FTSE ticker style (e.g. III.L) to EODHD style (III.LSE)
                if raw_ticker.upper().endswith(".L"):
                    base = raw_ticker[:-2]
                    ticker = f"{base}.LSE"
                elif "." in raw_ticker:
                    ticker = raw_ticker
                else:
                    ticker = f"{raw_ticker}.LSE"

                base_symbol = raw_ticker.split(".")[0]
                candidates = [
                    ticker.upper(),
                    raw_ticker.upper(),
                    base_symbol.upper(),
                    f"{base_symbol}.L".upper(),
                    f"{base_symbol}.LSE".upper(),
                ]
                existing = next((existing_map[c] for c in candidates if c in existing_map), None)

                data = dict(
                    company_name=company_name,
                    soctor=sector,
                    industry=normalized_industry,
                    industry_subgroup=subgroup,
                    description="",
                    country="UK",
                    website=None,
                    address="",
                )

                if existing:
                    for k, v in data.items():
                        if v in (None, ""):
                            continue
                        setattr(existing, k, v)
                    to_update.append(existing)
                else:
                    new_company = Company(ticker=ticker, **data)
                    to_create.append(new_company)
                    existing_map[ticker.upper()] = new_company

        update_fields = ["company_name", "soctor", "industry", "industry_subgroup", "description", "country", "website", "address"]
        with transaction.atomic():
            if to_create:
                Company.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                Company.objects.bulk_update(to_update, update_fields)

        self.stdout.write(self.style.SUCCESS(f"FTSE: Added {len(to_create)}, updated {len(to_update)} companies"))
