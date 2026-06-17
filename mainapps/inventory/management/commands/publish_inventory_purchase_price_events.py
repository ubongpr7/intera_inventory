from django.core.management.base import BaseCommand

from mainapps.orders.models import GoodsReceiptLine
from subapps.kafka.producers.inventory import publish_inventory_purchase_price_recorded


class Command(BaseCommand):
    help = "Publish inventory purchase-price events to Kafka from recorded goods receipts."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, default=None)
        parser.add_argument("--goods-receipt-line-id", default=None)

    def handle(self, *args, **options):
        profile_id = options["profile_id"]
        goods_receipt_line_id = options["goods_receipt_line_id"]

        queryset = GoodsReceiptLine.objects.select_related("inventory_item").order_by("created_at")
        if profile_id is not None:
            queryset = queryset.filter(inventory_item__profile_id=profile_id)
        if goods_receipt_line_id:
            queryset = queryset.filter(id=goods_receipt_line_id)

        published_count = 0
        skipped_count = 0

        for goods_receipt_line in queryset.iterator():
            envelope = publish_inventory_purchase_price_recorded(goods_receipt_line_id=goods_receipt_line.id)
            if envelope is None:
                skipped_count += 1
                continue
            published_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Published {published_count} inventory purchase-price events; skipped {skipped_count} unmapped lines."
            )
        )
