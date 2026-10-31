from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from scripts.check_naked_trader import ROOT, run


class Command(BaseCommand):
    help = 'Check published Naked Trader share trades and email new or updated entries.'

    def add_arguments(self, parser):
        parser.add_argument('--state', type=Path, default=ROOT / '.naked_trader' / 'state.json')
        parser.add_argument('--dry-run', action='store_true', help='Fetch and compare without email or baseline updates')

    def handle(self, *args, **options):
        try:
            run(options['state'], dry_run=options['dry_run'])
        except Exception as exc:
            # Provider exception text can expose email addresses or credentials.
            raise CommandError(
                f'Naked Trader checker failed ({type(exc).__name__}). '
                'Check the last progress message, volume permissions, network and email configuration.'
            ) from None
