from datetime import date, datetime
import json
from pathlib import Path
import tempfile
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from stocks.alerts.runner import due_slot, run_alerts
from stocks.alerts.weekly import rank_market, build_report, DataNotReady


class AlertTests(SimpleTestCase):
    def test_weekly_return_uses_prior_week_close_adjusted_and_excludes_missing(self):
        friday = date(2026, 10, 30)
        def price(ticker, day, close):
            return {'ticker': ticker, 'date': date(2026, 10, day), 'split_adjusted_price__close': close}
        rows = [price('A', 23, 50), price('A', 30, 55), price('B', 23, 100), price('B', 30, 120),
                price('MISSING', 30, 100), price('ZERO', 23, 0), price('ZERO', 30, 10),
                price('STALE', 23, 10), price('STALE', 29, 20), price('NAN', 23, float('nan')), price('NAN', 30, 10)]
        ranked, count = rank_market({t: t for t in ['A', 'B', 'MISSING', 'ZERO', 'STALE', 'NAN']}, rows, friday)
        self.assertEqual([r['ticker'] for r in ranked], ['B', 'A'])
        self.assertEqual(count, 2)
        self.assertAlmostEqual(ranked[0]['return'], 20)

    def test_daily_schedule_tracks_uk_daylight_time(self):
        schedule = {'frequency': 'daily', 'hours': [10, 16]}
        now = datetime(2026, 7, 3, 10, tzinfo=ZoneInfo('Europe/London'))
        self.assertTrue(due_slot(schedule, now).endswith('10:00:00+01:00'))
        self.assertIsNone(due_slot(schedule, now.replace(hour=9)))

    def test_weekly_due_after_friday_and_retry_over_weekend(self):
        schedule = {'frequency': 'weekly', 'weekday': 4, 'hour': 22}
        now = datetime(2026, 10, 30, 21, tzinfo=ZoneInfo('Europe/London'))
        self.assertIsNone(due_slot(schedule, now))
        self.assertEqual(due_slot(schedule, now.replace(day=31)), '2026-10-30T22:00:00+00:00')

    def test_runner_retry_isolated_failure_disabled_dedupe_and_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text(json.dumps({'timezone': 'Europe/London', 'alerts': {
                'naked_trader': {'enabled': True, 'schedule': {'frequency': 'daily', 'hours': [10, 16]}},
                'weekly_performers': {'enabled': True, 'schedule': {'frequency': 'weekly', 'weekday': 4, 'hour': 22}},
            }}))
            now = datetime(2026, 10, 30, 22, tzinfo=ZoneInfo('Europe/London'))
            naked, weekly = Mock(side_effect=OSError()), Mock()
            with patch.dict('stocks.alerts.runner.HANDLERS', {'naked_trader': naked, 'weekly_performers': weekly}):
                with self.assertRaises(RuntimeError):
                    run_alerts(config, root, now=now)
                self.assertEqual(weekly.call_count, 1)
                self.assertNotIn('naked_trader', json.loads((root / 'sent.json').read_text()))
                naked.side_effect = None
                run_alerts(config, root, now=now)
                self.assertEqual(weekly.call_count, 1)
                run_alerts(config, root, now=now)
                self.assertEqual(naked.call_count, 2)
                before = (root / 'sent.json').read_bytes()
                run_alerts(config, root, dry_run=True, now=now.replace(day=31))
                self.assertEqual(before, (root / 'sent.json').read_bytes())
                data = json.loads(config.read_text())
                data['alerts']['naked_trader']['enabled'] = False
                config.write_text(json.dumps(data))
                naked.reset_mock()
                run_alerts(config, root, now=now.replace(day=31))
                naked.assert_not_called()

    @patch('stocks.alerts.weekly.Price')
    @patch('stocks.alerts.weekly.Company')
    def test_report_groups_markets_and_rejects_absent_market(self, company, price):
        company.objects.values.return_value = [
            {'ticker': t, 'company_name': t, 'country': c} for t, c in [('A', 'USA'), ('B', 'UK'), ('C', 'India')]]
        price.objects.filter.return_value.values.return_value = [
            {'ticker': t, 'date': date(2026, 10, day), 'split_adjusted_price__close': close}
            for t in ['A', 'B', 'C'] for day, close in [(23, 100), (30, 110)]]
        subject, body = build_report(date(2026, 10, 30), {})
        self.assertIn('2026-10-30', subject)
        self.assertEqual(body.count('Eligible: 1/1'), 3)
        price.objects.filter.return_value.values.return_value = []
        with self.assertRaises(DataNotReady):
            build_report(date(2026, 10, 30), {})
