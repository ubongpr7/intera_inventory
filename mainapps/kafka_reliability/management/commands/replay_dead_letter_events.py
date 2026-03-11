from django.core.management.base import BaseCommand

from subapps.kafka.reliability import replay_dead_letter_events


class Command(BaseCommand):
    help = "Replay Kafka dead-letter events back to their original topics."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--event-id", default=None)

    def handle(self, *args, **options):
        replayed = replay_dead_letter_events(
            limit=options["limit"],
            event_id=options["event_id"],
        )
        self.stdout.write(self.style.SUCCESS(f"Replayed {replayed} dead-letter events."))
