from unittest import TestCase

from stocks.management.commands.enrich_companies_yahoo import missing, profile_values, yahoo_symbol


class YahooProfileTests(TestCase):
    def test_exchange_symbols_preserve_share_class(self):
        for stored, yahoo in [("AAPL.US", "AAPL"), ("BRK.B.US", "BRK-B"),
                              ("RELIANCE.NSE", "RELIANCE.NS"), ("III.LSE", "III.L"),
                              ("NIFTY.NSE", "^NSEI"), ("VOD.L", "VOD.L")]:
            self.assertEqual(yahoo_symbol(stored), yahoo)

    def test_profile_keeps_existing_industry_convention(self):
        values = profile_values({"sector": "Technology", "industry": "Software - Infrastructure",
                                 "address1": "1 Main St", "city": "London", "country": "United Kingdom"})
        self.assertEqual(values["industry"], "Technology")
        self.assertEqual(values["industry_subgroup"], "Software - Infrastructure")
        self.assertEqual(values["country"], "UK")
        self.assertEqual(values["address"], "1 Main St, London, United Kingdom")
        self.assertIsNone(values["description"])

    def test_placeholder_detection(self):
        for value in [None, "", "  ", "Unknown", "N/A", "nan"]:
            self.assertTrue(missing(value))
        self.assertFalse(missing("Apple Inc."))


class YahooCommandTests(TestCase):
    def run_command(self, dry_run):
        from io import StringIO
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from stocks.management.commands.enrich_companies_yahoo import Command

        company = SimpleNamespace(pk=42, ticker="AAPL.US", company_name="Existing name",
                                  description="", soctor="Technology", industry="Technology",
                                  industry_subgroup="Hardware", website=None, address="Existing address",
                                  country="USA", save=MagicMock())
        queryset = MagicMock()
        queryset.__iter__.return_value = iter([company])
        manager = MagicMock()
        manager.order_by.return_value = queryset
        manager.select_for_update.return_value.get.return_value = company
        with TemporaryDirectory() as directory, \
             patch('stocks.management.commands.enrich_companies_yahoo.Company.objects', manager), \
             patch('stocks.management.commands.enrich_companies_yahoo.transaction.atomic'), \
             patch('stocks.management.commands.enrich_companies_yahoo.cache'), \
             patch('stocks.management.commands.enrich_companies_yahoo.yf.Ticker') as ticker:
            ticker.return_value.get_info.return_value = {
                "longName": "Different name", "longBusinessSummary": "New description",
                "website": "https://apple.com", "address1": "New address", "sector": "Different sector",
            }
            command = Command(stdout=StringIO(), stderr=StringIO())
            command.handle(tickers=None, limit=None, sleep=0, retries=0, dry_run=dry_run,
                           audit_file=f"{directory}/audit.jsonl")
            import json
            with open(f"{directory}/audit.jsonl") as report:
                entry = json.loads(report.readline())
        self.assertEqual(company.company_name, "Existing name")
        self.assertEqual(company.address, "Existing address")
        self.assertEqual(company.soctor, "Technology")
        self.assertEqual(entry["changes"]["description"]["before"], "")
        return company

    def test_write_fills_only_missing_fields(self):
        company = self.run_command(False)
        self.assertEqual(company.description, "New description")
        self.assertEqual(company.website, "https://apple.com")
        company.save.assert_called_once_with(update_fields=["description", "website"])

    def test_dry_run_never_writes(self):
        company = self.run_command(True)
        self.assertEqual(company.description, "")
        company.save.assert_not_called()
