import time

from django.core.management.base import BaseCommand

from subapps.services.operational_automations import OperationalAutomationService


class Command(BaseCommand):
    help = 'Run workspace-scoped inventory and purchasing automations.'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int, default=None)
        parser.add_argument('--once', action='store_true', help='Run one evaluation and exit.')
        parser.add_argument('--interval', type=int, default=18000, help='Polling interval in seconds.')

    def handle(self, *args, **options):
        interval = max(300, options['interval'])
        while True:
            result = OperationalAutomationService.evaluate(profile_id=options['profile_id'])
            self.stdout.write(self.style.SUCCESS(
                'Operational automation evaluation: '
                f"reorder_evaluated={result['reorder_evaluated']} "
                f"reorder_created={result['reorder_created']} "
                f"expiry_alerts={result['expiry_alerts_created']} "
                f"po_alerts={result['po_alerts_created']} "
                f"stock_alerts={result['stock_alerts_created']} "
                f"availability_reconciled={result['availability_reconciled']} "
                f"availability_failed={result['availability_failed']}"
            ))
            if options['once']:
                return
            time.sleep(interval)
