from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from stocks.models import StockApiRateWindow


class Command(BaseCommand):
    help = "Remove stock API rate-limit rows unused for more than one day (run daily)."

    def handle(self, *args, **options):
        count, _ = StockApiRateWindow.objects.filter(updated_at__lt=timezone.now() - timedelta(days=1)).delete()
        self.stdout.write(f"Deleted {count} expired rate-limit rows.")
