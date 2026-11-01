import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from stocks.alerts.runner import DEFAULT_CONFIG, run_alerts


class Command(BaseCommand):
    help = 'Run enabled alerts that are due, tracking successful runs on persistent disk.'

    def add_arguments(self, parser):
        parser.add_argument('--config', type=Path, default=Path(os.environ.get('ALERT_CONFIG_PATH', DEFAULT_CONFIG)))
        parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('ALERT_STATE_DIR', '.alerts')))
        parser.add_argument('--only', choices=['naked_trader', 'weekly_performers'])
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write('Starting configured alerts...', ending='\n')
        self.stdout.flush()
        try:
            run_alerts(options['config'], options['state_dir'], options['dry_run'], options['only'])
        except Exception as exc:
            raise CommandError(f'Alert runner failed ({type(exc).__name__}); inspect progress logs.') from None
