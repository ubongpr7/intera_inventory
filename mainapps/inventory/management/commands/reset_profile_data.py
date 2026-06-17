from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, QuerySet

from mainapps.inventory.models import InventoryCategory, InventoryItem
from mainapps.projections.models import CatalogProductProjection, CatalogVariantProjection
from mainapps.orders.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLineItem,
    ReturnOrder,
    ReturnOrderLineItem,
    SalesOrder,
    SalesOrderLineItem,
    SalesOrderShipment,
    SalesOrderShipmentLine,
)
from mainapps.stock.models import (
    StockAdjustment,
    StockBalance,
    StockLocation,
    StockLot,
    StockMovement,
    StockReservation,
    StockSerial,
)


@dataclass(frozen=True)
class ResetEntry:
    label: str
    queryset: QuerySet


def _profile_q(profile_id: str) -> Q:
    return Q(profile_id=int(profile_id)) | Q(profile=str(profile_id))


class Command(BaseCommand):
    help = "Delete all profile-scoped inventory resources for a single workspace/profile."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", required=True, help="Company profile/workspace ID to reset.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without deleting anything.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Required to actually delete data.",
        )

    def handle(self, *args, **options):
        profile_id = str(options["profile_id"]).strip()
        dry_run = bool(options["dry_run"])
        force = bool(options["force"])
        if not profile_id:
            raise SystemExit("profile-id is required")
        if not dry_run and not force:
            raise SystemExit("Refusing to delete data without --force")

        tenant_profile_id = int(profile_id)
        profile_lookup = _profile_q(profile_id)
        q = _profile_q(profile_id)
        plan = [
            ResetEntry(
                "stock_adjustments",
                StockAdjustment.objects.filter(inventory_item__profile_id=tenant_profile_id),
            ),
            ResetEntry("stock_movements", StockMovement.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("stock_reservations", StockReservation.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("stock_balances", StockBalance.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("stock_serials", StockSerial.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("stock_lots", StockLot.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry(
                "sales_order_shipment_lines",
                SalesOrderShipmentLine.objects.filter(
                    Q(shipment__profile_id=tenant_profile_id) | Q(shipment__profile=str(profile_id))
                ),
            ),
            ResetEntry("sales_order_shipments", SalesOrderShipment.objects.filter(q)),
            ResetEntry(
                "goods_receipt_lines",
                GoodsReceiptLine.objects.filter(
                    Q(goods_receipt__profile_id=tenant_profile_id) | Q(goods_receipt__profile=str(profile_id))
                ),
            ),
            ResetEntry("goods_receipts", GoodsReceipt.objects.filter(q)),
            ResetEntry(
                "return_order_line_items",
                ReturnOrderLineItem.objects.filter(
                    Q(return_order__profile_id=tenant_profile_id) | Q(return_order__profile=str(profile_id))
                ),
            ),
            ResetEntry("return_orders", ReturnOrder.objects.filter(q)),
            ResetEntry(
                "sales_order_line_items",
                SalesOrderLineItem.objects.filter(
                    Q(sales_order__profile_id=tenant_profile_id) | Q(sales_order__profile=str(profile_id))
                ),
            ),
            ResetEntry("sales_orders", SalesOrder.objects.filter(q)),
            ResetEntry(
                "purchase_order_line_items",
                PurchaseOrderLineItem.objects.filter(
                    Q(purchase_order__profile_id=tenant_profile_id) | Q(purchase_order__profile=str(profile_id))
                ),
            ),
            ResetEntry("purchase_orders", PurchaseOrder.objects.filter(q)),
            ResetEntry("inventory_items", InventoryItem.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("catalog_variant_projections", CatalogVariantProjection.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("catalog_product_projections", CatalogProductProjection.objects.filter(profile_id=tenant_profile_id)),
            ResetEntry("inventory_categories", InventoryCategory.objects.filter(q)),
            ResetEntry("stock_locations", StockLocation.objects.filter(q)),
        ]

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Inventory reset plan for profile {profile_id}"))
            total_rows = 0
            for entry in plan:
                count = entry.queryset.count()
                total_rows += count
                self.stdout.write(f" - {entry.label}: {count}")
            self.stdout.write(self.style.WARNING(f"Dry run only. Total matching rows: {total_rows}"))
            return

        self.stdout.write(self.style.WARNING(f"Deleting inventory workspace data for profile {profile_id}"))
        for entry in plan:
            self.stdout.write(f" - {entry.label}")

        deleted_totals: dict[str, int] = {}
        with transaction.atomic():
            for entry in plan:
                deleted_count, _ = entry.queryset.delete()
                deleted_totals[entry.label] = deleted_count

        self.stdout.write(self.style.SUCCESS(f"Deleted inventory workspace data for profile {profile_id}"))
        for label, count in deleted_totals.items():
            self.stdout.write(f" - {label}: {count}")
