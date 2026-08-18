from django.core.cache import cache
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Run daily market refresh: EODHD prices -> technical indicators -> cache clear"

    def add_arguments(self, parser):
        parser.add_argument("--api-token", required=False, help="EODHD API token override")
        parser.add_argument("--default-exchange", default="US", help="Fallback exchange suffix")
        parser.add_argument("--sleep", type=float, default=0.1, help="Sleep between API calls")
        parser.add_argument("--years", type=int, default=20, help="History fetch depth for new tickers")
        parser.add_argument("--start-date", default=None, help="Optional backfill start date YYYY-MM-DD")
        parser.add_argument("--http-timeout", type=float, default=30.0, help="HTTP timeout per request")
        parser.add_argument("--http-retries", type=int, default=2, help="HTTP retry count")
        parser.add_argument("--http-backoff", type=float, default=1.5, help="HTTP retry backoff seconds")
        parser.add_argument("--technicals-days", type=int, default=60, help="Days window for technical recompute")
        parser.add_argument("--benchmark", default="SPY.US", help="Benchmark ticker for beta/alpha")
        parser.add_argument("--dry-run", action="store_true", help="Run without writing DB changes")

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Step 1/3: Updating EOD prices from EODHD"))

        update_kwargs = {
            "default_exchange": options["default_exchange"],
            "sleep": options["sleep"],
            "years": options["years"],
            "http_timeout": options["http_timeout"],
            "http_retries": options["http_retries"],
            "http_backoff": options["http_backoff"],
            "dry_run": options["dry_run"],
        }

        if options.get("api_token"):
            update_kwargs["api_token"] = options["api_token"]

        if options.get("start_date"):
            update_kwargs["start_date"] = options["start_date"]

        call_command("update_prices_eodhd", **update_kwargs)

        self.stdout.write(self.style.NOTICE("Step 2/3: Recomputing technical indicators"))
        call_command(
            "compute_technicals",
            days=options["technicals_days"],
            benchmark=options["benchmark"],
        )

        self.stdout.write(self.style.NOTICE("Step 3/3: Clearing response cache"))
        cache.clear()

        self.stdout.write(self.style.SUCCESS("Daily market refresh completed"))
