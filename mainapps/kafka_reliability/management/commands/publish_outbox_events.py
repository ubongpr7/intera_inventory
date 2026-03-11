from django.core.management.base import BaseCommand

from subapps.kafka.reliability import run_outbox_publisher


class Command(BaseCommand):
    help = "Publish pending Kafka outbox events."

    def add_arguments(self, parser):
        parser.add_argument("--duration", type=float, default=None)
        parser.add_argument("--poll-interval", type=float, default=None)
        parser.add_argument("--batch-size", type=int, default=None)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        run_outbox_publisher(
            run_duration=options["duration"],
            poll_interval=options["poll_interval"],
            batch_size=options["batch_size"],
            run_once=options["once"],
        )
