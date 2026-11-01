from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase


class DailyRefreshTests(SimpleTestCase):
    @patch('stocks.management.commands.run_daily_market_refresh.cache')
    @patch('stocks.management.commands.run_daily_market_refresh.call_command')
    def test_adjustments_follow_prices_before_indicators(self, command, cache):
        call_command('run_daily_market_refresh', api_token='test-token', skip_checks=True)
        self.assertEqual([c.args[0] for c in command.call_args_list],
                         ['update_prices_eodhd', 'backfill_split_adjusted_prices', 'compute_technicals'])
        self.assertEqual(command.call_args_list[1].kwargs['api_token'], 'test-token')
        self.assertFalse(command.call_args_list[1].kwargs['dry_run'])
        cache.clear.assert_called_once()

    @patch('stocks.management.commands.run_daily_market_refresh.cache')
    @patch('stocks.management.commands.run_daily_market_refresh.call_command')
    def test_failed_split_refresh_stops_pipeline(self, command, cache):
        command.side_effect = [None, CommandError('XYZ.US: split-history HTTP 404; no changes made for this ticker.')]
        with self.assertRaisesRegex(CommandError, 'XYZ.US: split-history HTTP 404'):
            call_command('run_daily_market_refresh', skip_checks=True)
        self.assertEqual(command.call_count, 2)
        cache.clear.assert_not_called()

    @patch('stocks.management.commands.run_daily_market_refresh.cache')
    @patch('stocks.management.commands.run_daily_market_refresh.call_command')
    def test_dry_run_does_not_compute_or_clear(self, command, cache):
        call_command('run_daily_market_refresh', dry_run=True, skip_checks=True)
        self.assertEqual(command.call_count, 2)
        self.assertTrue(all(c.kwargs['dry_run'] for c in command.call_args_list))
        cache.clear.assert_not_called()
