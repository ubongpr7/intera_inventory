from django.core.management.base import BaseCommand

from subapps.kafka.consumers.consumer import consume_events


class Command(BaseCommand):
    help = "Run the Kafka consumer loop for the inventory service."

    def add_arguments(self, parser):
        parser.add_argument("--duration", type=float, default=None)
        parser.add_argument("--poll-interval", type=float, default=None)

    def handle(self, *args, **options):
        consume_events(
            run_duration=options["duration"],
            poll_interval=options["poll_interval"],
        )
