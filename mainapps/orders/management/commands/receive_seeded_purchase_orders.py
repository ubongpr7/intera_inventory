from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from mainapps.orders.models import PurchaseOrder, PurchaseOrderStatus
from mainapps.stock.models import StockLocation
from subapps.services.stock_domain import StockDomainService


SEED_MARKER = "[seeded_by_codex:profile_po_seed_v1]"
RECEIPT_MARKER = "[received_by_codex:profile_po_seed_v1]"
ALLOCATION_RATIOS: tuple[tuple[str, Decimal], ...] = (
    ("Gberigbe Store", Decimal("0.50")),
    ("Airport Road, Oshodi Store", Decimal("0.30")),
    ("Agric, Ikorodu Store", Decimal("0.20")),
)


@dataclass(frozen=True)
class ReceiptTarget:
    structural_name: str
    structural_location_id: str
    receiving_bay_id: str


def _allocate(quantity: Decimal) -> list[int]:
    whole_quantity = int(quantity)
    allocated: list[int] = []
    running_total = 0
    for index, (_, ratio) in enumerate(ALLOCATION_RATIOS):
        if index == len(ALLOCATION_RATIOS) - 1:
            share = whole_quantity - running_total
        else:
            share = int((Decimal(whole_quantity) * ratio).to_integral_value(rounding="ROUND_FLOOR"))
            running_total += share
        allocated.append(share)
    return allocated


class Command(BaseCommand):
    help = "Receive the seeded purchase orders into each store's receiving bay and print the allocation breakdown."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, default=1)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--skip-publish",
            action="store_true",
            default=False,
            help="Skip Kafka publish hooks while receiving seeded stock.",
        )

    def handle(self, *args, **options):
        profile_id = options["profile_id"]
        dry_run = options["dry_run"]
        skip_publish = options["skip_publish"]

        targets = self._resolve_receipt_targets(profile_id=profile_id)
        purchase_orders = list(
            PurchaseOrder.objects.filter(
                profile_id=profile_id,
                notes__icontains=SEED_MARKER,
            ).prefetch_related("line_items__inventory_item").order_by("reference")
        )
        if not purchase_orders:
            raise CommandError(f"No seeded purchase orders were found for profile {profile_id}.")

        if dry_run:
            self._print_dry_run(purchase_orders=purchase_orders, targets=targets)
            return

        self._receive_purchase_orders(
            purchase_orders=purchase_orders,
            targets=targets,
            skip_publish=skip_publish,
        )

    def _resolve_receipt_targets(self, *, profile_id: int) -> list[ReceiptTarget]:
        targets: list[ReceiptTarget] = []
        for structural_name, _ratio in ALLOCATION_RATIOS:
            structural = StockLocation.objects.filter(
                profile_id=profile_id,
                name=structural_name,
                structural=True,
            ).first()
            if structural is None:
                raise CommandError(f"Structural location '{structural_name}' was not found.")
            receiving_bay = structural.get_children().filter(name="Receiving Bay").first()
            if receiving_bay is None:
                raise CommandError(f"Receiving Bay was not found under '{structural_name}'.")
            targets.append(
                ReceiptTarget(
                    structural_name=structural_name,
                    structural_location_id=str(structural.id),
                    receiving_bay_id=str(receiving_bay.id),
                )
            )
        return targets

    def _print_dry_run(self, *, purchase_orders, targets: list[ReceiptTarget]) -> None:
        for purchase_order in purchase_orders:
            self.stdout.write(f"PO|{purchase_order.reference}")
            for line in purchase_order.line_items.all().order_by("inventory_item__name_snapshot"):
                allocations = _allocate(Decimal(str(line.quantity)))
                rendered = ", ".join(
                    f"{target.structural_name}: {allocations[index]}"
                    for index, target in enumerate(targets)
                )
                self.stdout.write(
                    f"  LINE|{line.inventory_item.name_snapshot}|ordered={line.quantity}|allocations={rendered}"
                )

    @transaction.atomic
    def _receive_purchase_orders(self, *, purchase_orders, targets: list[ReceiptTarget], skip_publish: bool) -> None:
        original_availability_publish = StockDomainService._publish_inventory_availability_on_commit
        original_purchase_price_publish = StockDomainService._publish_inventory_purchase_price_on_commit
        if skip_publish:
            StockDomainService._publish_inventory_availability_on_commit = classmethod(lambda cls, inventory_item_id: None)
            StockDomainService._publish_inventory_purchase_price_on_commit = classmethod(lambda cls, goods_receipt_line_id: None)

        try:
            self._receive_purchase_orders_inner(purchase_orders=purchase_orders, targets=targets)
        finally:
            if skip_publish:
                StockDomainService._publish_inventory_availability_on_commit = original_availability_publish
                StockDomainService._publish_inventory_purchase_price_on_commit = original_purchase_price_publish

    @transaction.atomic
    def _receive_purchase_orders_inner(self, *, purchase_orders, targets: list[ReceiptTarget]) -> None:
        for purchase_order in purchase_orders:
            if RECEIPT_MARKER in (purchase_order.notes or ""):
                self.stdout.write(self.style.WARNING(f"Skipping {purchase_order.reference}: already received by seed command."))
                continue

            goods_receipt = StockDomainService.create_goods_receipt(
                purchase_order=purchase_order,
                actor_user_id=None,
                notes=f"{RECEIPT_MARKER} Distributed across structural locations.",
            )

            for line in purchase_order.line_items.all().order_by("inventory_item__name_snapshot"):
                allocations = _allocate(Decimal(str(line.quantity)))
                for index, target in enumerate(targets):
                    share = allocations[index]
                    if share <= 0:
                        continue
                    receiving_bay = StockLocation.objects.get(id=target.receiving_bay_id)
                    StockDomainService.receive_purchase_line(
                        purchase_order=purchase_order,
                        line_item=line,
                        stock_location=receiving_bay,
                        quantity_received=Decimal(str(share)),
                        actor_user_id=None,
                        goods_receipt=goods_receipt,
                        notes=(
                            f"{RECEIPT_MARKER} "
                            f"Allocated to {target.structural_name} via {receiving_bay.name}."
                        ),
                    )
                    self.stdout.write(
                        f"RECEIVED|{purchase_order.reference}|{line.inventory_item.name_snapshot}|"
                        f"{target.structural_name}|bay={receiving_bay.name}|qty={share}"
                    )

            purchase_order.status = PurchaseOrderStatus.RECEIVED
            purchase_order.received_date = timezone.now().date()
            purchase_order.workflow_state = "FULLY_RECEIVED"
            purchase_order.complete_date = timezone.now()
            purchase_order.notes = f"{purchase_order.notes}\n{RECEIPT_MARKER}".strip()
            purchase_order.save()
            self.stdout.write(self.style.SUCCESS(f"UPDATED|{purchase_order.reference}|status={purchase_order.status}|workflow={purchase_order.workflow_state}"))
