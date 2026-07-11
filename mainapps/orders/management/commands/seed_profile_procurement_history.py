from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from mainapps.company.models import Company
from mainapps.inventory.models import InventoryItem
from mainapps.orders.models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLineItem, PurchaseOrderStatus
from mainapps.stock.models import StockBalance, StockLocation, StockLot, StockLotStatus, StockMovement, StockMovementType


SEED_MARKER = "[seeded_by_codex:profile_procurement_history_v1]"
ADJUSTMENT_MARKER = "[seeded_by_codex:profile_sell_through_adjustment_v1]"
LAGOS_TZ = timezone.get_current_timezone()
MONTH_SEASONALITY = {
    1: Decimal("0.84"),
    2: Decimal("0.80"),
    3: Decimal("0.88"),
    4: Decimal("0.93"),
    5: Decimal("0.99"),
    6: Decimal("1.05"),
    7: Decimal("1.00"),
    8: Decimal("0.97"),
    9: Decimal("1.06"),
    10: Decimal("1.12"),
    11: Decimal("1.20"),
    12: Decimal("1.30"),
}
STORE_RATIOS = (
    ("Gberigbe Store", Decimal("0.50")),
    ("Airport Road, Oshodi Store", Decimal("0.30")),
    ("Agric, Ikorodu Store", Decimal("0.20")),
)
MONTHLY_PURCHASE_ORDER_COUNT = 4
MAX_LINES_PER_PO = {
    "Multipro Consumer Products Ltd": 4,
    "Daily Needs Distribution Ltd": 3,
    "StyleRack Apparel Wholesale Ltd": 2,
    "Satnam Investment Nigeria Ltd": 1,
}


@dataclass(frozen=True)
class SupplierSeed:
    name: str
    email: str
    address: str
    website: str


@dataclass(frozen=True)
class InventorySeed:
    inventory_name: str
    supplier_name: str
    family: str
    unit_cost: Decimal
    demand_weight: Decimal
    hot_months: tuple[int, ...]


SUPPLIERS: tuple[SupplierSeed, ...] = (
    SupplierSeed("Multipro Consumer Products Ltd", "buyers+multipro@drabtech.local", "Ikeja Trade Axis, Lagos", "https://multipro.example.local"),
    SupplierSeed("Daily Needs Distribution Ltd", "buyers+dailyneeds@drabtech.local", "Oregun Industrial Estate, Lagos", "https://dailyneeds.example.local"),
    SupplierSeed("StyleRack Apparel Wholesale Ltd", "buyers+stylerack@drabtech.local", "Trade Fair Complex, Lagos", "https://stylerack.example.local"),
    SupplierSeed("Satnam Investment Nigeria Ltd", "buyers+satnam@drabtech.local", "Lagos Island, Lagos", "https://satnam.example.local"),
)

ITEMS: tuple[InventorySeed, ...] = (
    InventorySeed("Eva Premium Water 75cl", "Multipro Consumer Products Ltd", "beverage", Decimal("240.00"), Decimal("1.25"), (3, 4, 5, 6, 10, 11, 12)),
    InventorySeed("Coca-Cola Original Taste 50cl", "Multipro Consumer Products Ltd", "beverage", Decimal("300.00"), Decimal("1.10"), (3, 4, 5, 6, 10, 11, 12)),
    InventorySeed("Fanta Orange 50cl", "Multipro Consumer Products Ltd", "beverage", Decimal("300.00"), Decimal("1.02"), (3, 4, 5, 6, 9, 10, 11, 12)),
    InventorySeed("Cabin Biscuit 200g", "Multipro Consumer Products Ltd", "grocery", Decimal("640.00"), Decimal("0.72"), (1, 2, 8, 9, 10, 11)),
    InventorySeed("Indomie Chicken Noodles 70g", "Multipro Consumer Products Ltd", "grocery", Decimal("250.00"), Decimal("1.18"), (1, 2, 3, 7, 8, 9)),
    InventorySeed("Chivita Orange Juice 1L", "Multipro Consumer Products Ltd", "beverage", Decimal("1280.00"), Decimal("0.48"), (4, 5, 6, 10, 11, 12)),
    InventorySeed("Golden Morn Cereal 900g", "Multipro Consumer Products Ltd", "grocery", Decimal("2150.00"), Decimal("0.28"), (1, 2, 3, 9, 10, 11)),
    InventorySeed("Mama Gold Rice 5kg", "Multipro Consumer Products Ltd", "grocery", Decimal("7600.00"), Decimal("0.12"), (4, 5, 9, 10, 11, 12)),
    InventorySeed("Maggi Star Seasoning Cubes 100ct", "Multipro Consumer Products Ltd", "grocery", Decimal("1540.00"), Decimal("0.22"), (2, 3, 4, 8, 9, 10)),
    InventorySeed("Nivea Radiant Body Lotion 400ml", "Daily Needs Distribution Ltd", "beauty", Decimal("3290.00"), Decimal("0.20"), (3, 4, 5, 10, 11, 12)),
    InventorySeed("Nivea Men Revitalizing Lotion 400ml", "Daily Needs Distribution Ltd", "beauty", Decimal("2460.00"), Decimal("0.16"), (3, 4, 5, 9, 10, 11)),
    InventorySeed("Nivea Creme Tin 150ml", "Daily Needs Distribution Ltd", "beauty", Decimal("1680.00"), Decimal("0.15"), (1, 2, 3, 8, 9, 10)),
    InventorySeed("Dettol Antiseptic Liquid 250ml", "Daily Needs Distribution Ltd", "beauty", Decimal("2050.00"), Decimal("0.13"), (2, 3, 4, 8, 9, 10)),
    InventorySeed("Johnson Baby Oil 200ml", "Daily Needs Distribution Ltd", "beauty", Decimal("2280.00"), Decimal("0.12"), (1, 2, 3, 7, 8, 9)),
    InventorySeed("Huggies Pure Baby Wipes 64ct", "Daily Needs Distribution Ltd", "beauty", Decimal("1770.00"), Decimal("0.10"), (1, 2, 3, 8, 9, 10)),
    InventorySeed("Colgate Triple Action Toothpaste 100ml", "Daily Needs Distribution Ltd", "beauty", Decimal("1240.00"), Decimal("0.11"), (1, 2, 3, 4, 8, 9)),
    InventorySeed("Afnan 9PM Eau De Parfum 100ml", "Satnam Investment Nigeria Ltd", "fragrance", Decimal("36100.00"), Decimal("0.05"), (2, 11, 12)),
    InventorySeed("Next Pique Polo Shirt Black L", "StyleRack Apparel Wholesale Ltd", "fashion", Decimal("24400.00"), Decimal("0.06"), (8, 9, 10, 11, 12)),
    InventorySeed("Next Pique Polo Shirt Navy M", "StyleRack Apparel Wholesale Ltd", "fashion", Decimal("24400.00"), Decimal("0.07"), (8, 9, 10, 11, 12)),
)

LOCATION_BY_FAMILY = {
    "beverage": "Beverage Aisle",
    "grocery": "Backroom",
    "beauty": "Beauty & Fragrance Gondola",
    "fashion": "Fashion Rack A",
    "fragrance": "Beauty & Fragrance Gondola",
}


def _month_start(anchor: date, months_ago: int) -> date:
    year = anchor.year
    month = anchor.month - months_ago
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, 1)


def _aware_datetime(day: date, *, hour: int, minute: int) -> datetime:
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute), LAGOS_TZ)


def _target_units_for_month(*, month_start: date, month_index: int, min_units: int, max_units: int) -> int:
    midpoint = Decimal(str((min_units + max_units) / 2))
    seasonal = MONTH_SEASONALITY[month_start.month]
    year_growth = Decimal("1.00") + (Decimal("0.05") * Decimal(str(month_index // 12)))
    phase = Decimal(str((((month_index * 17) % 9) - 4) / 100))
    jitter = Decimal("1.00") + phase
    target = int((midpoint * seasonal * year_growth * jitter).to_integral_value(rounding=ROUND_HALF_UP))
    return max(min_units, min(max_units, target))


class Command(BaseCommand):
    help = "Seed two years of supplier-driven purchase orders, goods receipts, and aggregate sell-through adjustments for the FFG debug workspace."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True)
        parser.add_argument("--months", type=int, default=24)
        parser.add_argument("--month-offset", type=int)
        parser.add_argument("--min-monthly-units", type=int, default=8000)
        parser.add_argument("--max-monthly-units", type=int, default=13000)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-publish", action="store_true", default=False)
        parser.add_argument("--replace-generated", action="store_true")

    def handle(self, *args, **options):
        profile_id = int(options["profile_id"])
        months = max(1, int(options["months"]))
        month_offset = options.get("month_offset")
        min_monthly_units = max(1000, int(options["min_monthly_units"]))
        max_monthly_units = max(min_monthly_units, int(options["max_monthly_units"]))
        dry_run = bool(options["dry_run"])
        skip_publish = bool(options["skip_publish"])
        replace_generated = bool(options["replace_generated"])

        if (
            month_offset is None
            and PurchaseOrder.objects.filter(profile_id=profile_id, notes__icontains=SEED_MARKER).exists()
            and not dry_run
            and not replace_generated
        ):
            raise CommandError("Seeded procurement history already exists for this profile. Run once on a clean workspace copy.")

        suppliers = self._ensure_suppliers(profile_id=profile_id, dry_run=dry_run)
        inventory_items = self._inventory_items(profile_id=profile_id)
        store_locations = self._store_locations(profile_id=profile_id)
        month_offsets = [int(month_offset)] if month_offset is not None else list(range(months - 1, -1, -1))
        month_starts = [_month_start(timezone.localdate(), months_ago) for months_ago in month_offsets]

        if dry_run:
            for month_index, month_start in enumerate(month_starts):
                target_units = _target_units_for_month(
                    month_start=month_start,
                    month_index=month_index,
                    min_units=min_monthly_units,
                    max_units=max_monthly_units,
                )
                self.stdout.write(f"DRY-RUN|month={month_start.isoformat()}|target_units={target_units}|purchase_orders=4")
            return

        try:
            total_purchase_orders = 0
            total_receipts = 0
            for month_index, month_start in enumerate(month_starts):
                if replace_generated:
                    month_filter = PurchaseOrder.objects.filter(profile_id=profile_id, notes__icontains=SEED_MARKER).filter(
                        notes__icontains=f"month={month_start.isoformat()}"
                    )
                    month_deleted = month_filter.count()
                    if month_deleted:
                        month_filter.delete()
                        StockMovement.objects.filter(
                            profile_id=profile_id,
                            movement_type="adjustment",
                            notes__icontains=ADJUSTMENT_MARKER,
                        ).filter(notes__icontains=month_start.strftime("%B %Y")).delete()
                        self.stdout.write(
                            self.style.WARNING(
                                f"Deleted {month_deleted} previously generated purchase order(s) for {month_start.isoformat()}."
                            )
                        )
                elif self._seeded_purchase_order_count_for_month(profile_id=profile_id, month_start=month_start) >= MONTHLY_PURCHASE_ORDER_COUNT:
                    self.stdout.write(
                        self.style.WARNING(f"SKIPPED|month={month_start.isoformat()}|reason=already_seeded")
                    )
                    continue
                target_units = _target_units_for_month(
                    month_start=month_start,
                    month_index=month_index,
                    min_units=min_monthly_units,
                    max_units=max_monthly_units,
                )
                po_count, receipt_count = self._seed_month(
                    profile_id=profile_id,
                    month_start=month_start,
                    month_index=month_index,
                    target_units=target_units,
                    suppliers=suppliers,
                    inventory_items=inventory_items,
                    store_locations=store_locations,
                )
                total_purchase_orders += po_count
                total_receipts += receipt_count
                self.stdout.write(
                    self.style.SUCCESS(
                        f"SEEDED|month={month_start.isoformat()}|purchase_orders={po_count}|goods_receipts={receipt_count}|target_units={target_units}"
                    )
                )
        finally:
            pass

        self.stdout.write(
            self.style.SUCCESS(
                f"Created seeded procurement history for profile {profile_id}: {total_purchase_orders} purchase orders and {total_receipts} goods receipts."
            )
        )

    def _ensure_suppliers(self, *, profile_id: int, dry_run: bool) -> dict[str, Company]:
        suppliers: dict[str, Company] = {}
        for supplier_seed in SUPPLIERS:
            existing = Company.objects.filter(profile_id=profile_id, name=supplier_seed.name, is_supplier=True).first()
            if existing is not None:
                suppliers[supplier_seed.name] = existing
                continue
            if dry_run:
                suppliers[supplier_seed.name] = Company(profile_id=profile_id, profile=str(profile_id), name=supplier_seed.name, is_supplier=True, currency="NGN")
                continue
            supplier, _ = Company.objects.update_or_create(
                profile_id=profile_id,
                name=supplier_seed.name,
                defaults={
                    "profile": str(profile_id),
                    "description": "Seeded supplier for long-range procurement history.",
                    "email": supplier_seed.email,
                    "short_address": supplier_seed.address,
                    "website": supplier_seed.website,
                    "link": supplier_seed.website,
                    "is_supplier": True,
                    "currency": "NGN",
                },
            )
            suppliers[supplier_seed.name] = supplier
        return suppliers

    def _inventory_items(self, *, profile_id: int) -> dict[str, InventoryItem]:
        items = {item.name_snapshot: item for item in InventoryItem.objects.filter(profile_id=profile_id)}
        missing = [seed.inventory_name for seed in ITEMS if seed.inventory_name not in items]
        if missing:
            raise CommandError(f"Missing inventory items for procurement seed: {missing}")
        return items

    def _store_locations(self, *, profile_id: int) -> dict[tuple[str, str], StockLocation]:
        locations: dict[tuple[str, str], StockLocation] = {}
        for structural_name, _ratio in STORE_RATIOS:
            structural = StockLocation.objects.filter(profile_id=profile_id, name=structural_name, structural=True).first()
            if structural is None:
                raise CommandError(f"Structural location '{structural_name}' was not found.")
            for family, child_name in LOCATION_BY_FAMILY.items():
                child = structural.get_descendants().filter(name=child_name).first()
                if child is None:
                    raise CommandError(f"Location '{child_name}' was not found under '{structural_name}'.")
                locations[(structural_name, family)] = child
        return locations

    def _seed_month(
        self,
        *,
        profile_id: int,
        month_start: date,
        month_index: int,
        target_units: int,
        suppliers: dict[str, Company],
        inventory_items: dict[str, InventoryItem],
        store_locations: dict[tuple[str, str], StockLocation],
    ) -> tuple[int, int]:
        monthly_demand = self._monthly_demand(
            month_start=month_start,
            month_index=month_index,
            target_units=target_units,
        )

        po_days = (3, 10, 18, 25)
        po_specs = [
            ("Multipro Consumer Products Ltd", ("beverage", "grocery")),
            ("Daily Needs Distribution Ltd", ("beauty",)),
            ("StyleRack Apparel Wholesale Ltd", ("fashion",)),
            ("Satnam Investment Nigeria Ltd", ("fragrance",)),
        ]

        created_purchase_orders = 0
        created_receipts = 0
        purchase_order_index = 0

        for po_day, (supplier_name, families) in zip(po_days, po_specs):
            purchase_order_slot = purchase_order_index + 1
            goods_receipt_reference = f"A2A-GR-{profile_id}-{month_start.strftime('%Y%m')}-{purchase_order_slot:02d}"
            if GoodsReceipt.objects.filter(profile_id=profile_id, reference=goods_receipt_reference).exists():
                purchase_order_index += 1
                continue

            issue_date = min(po_day, monthrange(month_start.year, month_start.month)[1])
            issue_at = _aware_datetime(date(month_start.year, month_start.month, issue_date), hour=9 + purchase_order_index, minute=10)
            delivery_at = issue_at + timedelta(days=4 + (purchase_order_index % 3))
            supplier = suppliers[supplier_name]

            with transaction.atomic():
                po = PurchaseOrder.objects.create(
                    profile_id=profile_id,
                    profile=str(profile_id),
                    supplier=supplier,
                    reference=f"A2A-PO-{profile_id}-{month_start.strftime('%Y%m')}-{purchase_order_slot:02d}",
                    order_currency="NGN",
                    status=PurchaseOrderStatus.RECEIVED,
                    workflow_state="FULLY_RECEIVED",
                    issue_date=issue_at,
                    delivery_date=delivery_at.date(),
                    received_date=delivery_at.date(),
                    complete_date=delivery_at,
                    approved_at=issue_at + timedelta(hours=2),
                    approved_by_user_id=7,
                    responsible_user_id=7,
                    description=f"{supplier_name} replenishment for {month_start.strftime('%B %Y')}",
                    notes=f"{SEED_MARKER}\nmonth={month_start.isoformat()}\nsupplier={supplier_name}",
                    budget_code=f"PROC-{month_start.strftime('%Y%m')}-{purchase_order_slot:02d}",
                    department="Operations",
                    supplier_reference=f"FFG-{month_start.strftime('%Y%m')}-{purchase_order_slot:02d}",
                )
                PurchaseOrder.objects.filter(pk=po.pk).update(created_at=issue_at, updated_at=delivery_at)

                line_items: list[PurchaseOrderLineItem] = []
                selected_seeds = self._select_purchase_order_items(
                    supplier_name=supplier_name,
                    families=families,
                    month_index=month_index,
                    purchase_order_index=purchase_order_index,
                    monthly_demand=monthly_demand,
                )
                for seed in selected_seeds:
                    monthly_units = monthly_demand.get(seed.inventory_name, 0)
                    if monthly_units <= 0:
                        continue
                    ordered_qty = max(1, int((Decimal(str(monthly_units)) * Decimal("1.10")).to_integral_value(rounding=ROUND_HALF_UP)))
                    item = inventory_items[seed.inventory_name]
                    line = PurchaseOrderLineItem.objects.create(
                        purchase_order=po,
                        inventory_item=item,
                        quantity=ordered_qty,
                        unit_price=seed.unit_cost,
                        discount_rate=Decimal("0.00"),
                        tax_rate=Decimal("0.00"),
                        description=f"{SEED_MARKER} Historical inbound stock for {month_start.strftime('%B %Y')}",
                    )
                    PurchaseOrderLineItem.objects.filter(pk=line.pk).update(created_at=issue_at, updated_at=delivery_at)
                    line_items.append(line)

                goods_receipt = GoodsReceipt.objects.create(
                    profile_id=profile_id,
                    profile=str(profile_id),
                    purchase_order=po,
                    supplier=supplier,
                    reference=goods_receipt_reference,
                    received_at=delivery_at,
                    received_by_user_id=7,
                    notes=f"{SEED_MARKER} Received into operational locations.",
                    created_by_user_id=7,
                    updated_by_user_id=7,
                )
                GoodsReceipt.objects.filter(pk=goods_receipt.pk).update(
                    received_at=delivery_at,
                    created_at=delivery_at,
                    updated_at=delivery_at,
                )

                for line_index, line in enumerate(line_items, start=1):
                    family = next(seed.family for seed in ITEMS if seed.inventory_name == line.inventory_item.name_snapshot)
                    structural_name = STORE_RATIOS[(purchase_order_index + line_index + month_index) % len(STORE_RATIOS)][0]
                    stock_location = store_locations[(structural_name, family)]
                    receipt_result = self._record_receipt_allocation(
                        profile_id=profile_id,
                        goods_receipt=goods_receipt,
                        purchase_order=po,
                        purchase_order_line=line,
                        inventory_item=line.inventory_item,
                        stock_location=stock_location,
                        quantity_received=Decimal(str(line.quantity)),
                        unit_cost=line.unit_price,
                        occurred_at=delivery_at,
                        lot_number=f"{month_start.strftime('%Y%m')}-{line.inventory_item.sku_snapshot}-{structural_name[:3].upper()}",
                        notes=f"{SEED_MARKER} Allocated to {structural_name} for {family}.",
                    )
                    goods_receipt_line = receipt_result["goods_receipt_line"]
                    GoodsReceiptLine.objects.filter(pk=goods_receipt_line.pk).update(created_at=delivery_at, updated_at=delivery_at)
                    stock_lot = receipt_result.get("stock_lot")
                    if stock_lot is not None:
                        StockLot.objects.filter(pk=stock_lot.pk).update(created_at=delivery_at, updated_at=delivery_at)
                    StockMovement.objects.filter(reference_id=str(goods_receipt_line.id)).update(
                        occurred_at=delivery_at,
                        created_at=delivery_at,
                        updated_at=delivery_at,
                    )

                created_purchase_orders += 1
                created_receipts += 1
                purchase_order_index += 1

        if not self._month_has_sell_through(profile_id=profile_id, month_start=month_start):
            self._apply_monthly_sell_through(
                profile_id=profile_id,
                month_start=month_start,
                demand=monthly_demand,
                inventory_items=inventory_items,
                store_locations=store_locations,
                month_index=month_index,
            )

        return created_purchase_orders, created_receipts

    def _seeded_purchase_order_count_for_month(self, *, profile_id: int, month_start: date) -> int:
        return PurchaseOrder.objects.filter(profile_id=profile_id, notes__icontains=SEED_MARKER).filter(
            notes__icontains=f"month={month_start.isoformat()}"
        ).count()

    def _month_has_sell_through(self, *, profile_id: int, month_start: date) -> bool:
        return StockMovement.objects.filter(
            profile_id=profile_id,
            movement_type=StockMovementType.ADJUSTMENT,
            notes__icontains=ADJUSTMENT_MARKER,
        ).filter(notes__icontains=month_start.strftime("%B %Y")).exists()

    def _select_purchase_order_items(
        self,
        *,
        supplier_name: str,
        families: tuple[str, ...],
        month_index: int,
        purchase_order_index: int,
        monthly_demand: dict[str, int],
    ) -> list[InventorySeed]:
        eligible = [
            seed
            for seed in ITEMS
            if seed.supplier_name == supplier_name and seed.family in families and monthly_demand.get(seed.inventory_name, 0) > 0
        ]
        eligible.sort(key=lambda seed: (-monthly_demand.get(seed.inventory_name, 0), seed.inventory_name))
        if not eligible:
            return []
        rotation = (month_index + purchase_order_index) % len(eligible)
        rotated = eligible[rotation:] + eligible[:rotation]
        limit = MAX_LINES_PER_PO.get(supplier_name, max(1, len(rotated)))
        return rotated[:limit]

    def _monthly_demand(self, *, month_start: date, month_index: int, target_units: int) -> dict[str, int]:
        raw_weights: dict[str, Decimal] = {}
        for seed in ITEMS:
            weight = seed.demand_weight
            if month_start.month in seed.hot_months:
                weight *= Decimal("1.35")
            if seed.family == "fashion" and month_start.month in {8, 9, 10, 11, 12}:
                weight *= Decimal("1.10")
            if seed.family == "fragrance" and month_start.month in {11, 12}:
                weight *= Decimal("1.15")
            year_bias = Decimal("1.00") + (Decimal("0.03") * Decimal(str(month_index // 12)))
            raw_weights[seed.inventory_name] = weight * year_bias

        total_weight = sum(raw_weights.values(), Decimal("0"))
        demand: dict[str, int] = {}
        allocated = 0
        seed_list = list(ITEMS)
        for index, seed in enumerate(seed_list):
            if index == len(seed_list) - 1:
                quantity = max(1, target_units - allocated)
            else:
                quantity = int(
                    (Decimal(str(target_units)) * (raw_weights[seed.inventory_name] / total_weight)).to_integral_value(
                        rounding=ROUND_HALF_UP
                    )
                )
                allocated += quantity
            demand[seed.inventory_name] = max(1, quantity)
        return demand

    def _allocate_quantity(self, quantity: int) -> list[int]:
        allocations: list[int] = []
        running_total = 0
        for index, (_name, ratio) in enumerate(STORE_RATIOS):
            if index == len(STORE_RATIOS) - 1:
                share = quantity - running_total
            else:
                share = int((Decimal(str(quantity)) * ratio).to_integral_value(rounding="ROUND_FLOOR"))
                running_total += share
            allocations.append(share)
        return allocations

    def _apply_monthly_sell_through(
        self,
        *,
        profile_id: int,
        month_start: date,
        demand: dict[str, int],
        inventory_items: dict[str, InventoryItem],
        store_locations: dict[tuple[str, str], StockLocation],
        month_index: int,
    ) -> None:
        sales_day = min(27 + (month_index % 2), monthrange(month_start.year, month_start.month)[1])
        sold_at = _aware_datetime(date(month_start.year, month_start.month, sales_day), hour=18, minute=20)
        for seed in ITEMS:
            family = seed.family
            item = inventory_items[seed.inventory_name]
            allocations = self._allocate_quantity(demand[seed.inventory_name])
            for (structural_name, _ratio), share in zip(STORE_RATIOS, allocations):
                if share <= 0:
                    continue
                stock_location = store_locations[(structural_name, family)]
                reason = (
                    f"{ADJUSTMENT_MARKER} Simulated sell-through for {month_start.strftime('%B %Y')} "
                    f"at {structural_name}."
                )
                self._consume_stock(
                    profile_id=profile_id,
                    inventory_item=item,
                    stock_location=stock_location,
                    quantity=Decimal(str(share)),
                    occurred_at=sold_at,
                    reason=reason,
                )

    def _record_receipt_allocation(
        self,
        *,
        profile_id: int,
        goods_receipt: GoodsReceipt,
        purchase_order: PurchaseOrder,
        purchase_order_line: PurchaseOrderLineItem,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        quantity_received: Decimal,
        unit_cost: Decimal,
        occurred_at: datetime,
        lot_number: str,
        notes: str,
    ) -> dict[str, object]:
        goods_receipt_line = GoodsReceiptLine.objects.create(
            goods_receipt=goods_receipt,
            purchase_order_line=purchase_order_line,
            inventory_item=inventory_item,
            stock_location=stock_location,
            received_quantity=quantity_received,
            unit_cost=unit_cost,
            lot_number=lot_number,
            created_by_user_id=7,
            updated_by_user_id=7,
        )
        GoodsReceiptLine.objects.filter(pk=goods_receipt_line.pk).update(created_at=occurred_at, updated_at=occurred_at)

        stock_lot = StockLot.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            supplier=purchase_order.supplier,
            purchase_order_line=purchase_order_line,
            goods_receipt_line=goods_receipt_line,
            lot_number=lot_number,
            unit_cost=unit_cost,
            currency_code=purchase_order.order_currency or "NGN",
            received_quantity=quantity_received,
            remaining_quantity=quantity_received,
            status=StockLotStatus.OPEN,
            created_by_user_id=7,
            updated_by_user_id=7,
        )
        StockLot.objects.filter(pk=stock_lot.pk).update(created_at=occurred_at, updated_at=occurred_at)

        balance = StockBalance.objects.filter(
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot__isnull=True,
        ).order_by("created_at", "id").first()
        if balance is None:
            balance = StockBalance.objects.create(
                profile_id=profile_id,
                inventory_item=inventory_item,
                stock_location=stock_location,
                quantity_on_hand=Decimal("0"),
                quantity_reserved=Decimal("0"),
                created_by_user_id=7,
                updated_by_user_id=7,
            )
        balance.quantity_on_hand = Decimal(str(balance.quantity_on_hand)) + quantity_received
        balance.updated_by_user_id = 7
        balance.save()
        StockBalance.objects.filter(pk=balance.pk).update(updated_at=occurred_at)

        movement = StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
            from_location=None,
            to_location=stock_location,
            movement_type=StockMovementType.RECEIPT,
            quantity=quantity_received,
            unit_cost=unit_cost,
            reference_type="goods_receipt_line",
            reference_id=str(goods_receipt_line.id),
            actor_user_id=7,
            occurred_at=occurred_at,
            notes=notes,
            created_by_user_id=7,
            updated_by_user_id=7,
        )
        StockMovement.objects.filter(pk=movement.pk).update(created_at=occurred_at, updated_at=occurred_at)
        return {"goods_receipt_line": goods_receipt_line, "stock_lot": stock_lot}

    def _consume_stock(
        self,
        *,
        profile_id: int,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        quantity: Decimal,
        occurred_at: datetime,
        reason: str,
    ) -> None:
        balance = StockBalance.objects.filter(
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot__isnull=True,
        ).first()
        if balance is None:
            balance = StockBalance.objects.create(
                profile_id=profile_id,
                inventory_item=inventory_item,
                stock_location=stock_location,
                quantity_on_hand=Decimal("0"),
                quantity_reserved=Decimal("0"),
                created_by_user_id=7,
                updated_by_user_id=7,
            )
        balance.quantity_on_hand = max(Decimal("0"), Decimal(str(balance.quantity_on_hand)) - quantity)
        balance.updated_by_user_id = 7
        balance.save()
        StockBalance.objects.filter(pk=balance.pk).update(updated_at=occurred_at)

        remaining = quantity
        for lot in StockLot.objects.filter(
            profile_id=profile_id,
            inventory_item=inventory_item,
            goods_receipt_line__stock_location=stock_location,
            remaining_quantity__gt=0,
        ).order_by("created_at", "id"):
            if remaining <= 0:
                break
            lot_remaining = Decimal(str(lot.remaining_quantity))
            deduction = min(lot_remaining, remaining)
            lot.remaining_quantity = lot_remaining - deduction
            lot.status = StockLotStatus.OPEN if lot.remaining_quantity > 0 else StockLotStatus.DEPLETED
            lot.updated_by_user_id = 7
            lot.save(update_fields=["remaining_quantity", "status", "updated_by_user_id", "updated_at"])
            StockLot.objects.filter(pk=lot.pk).update(updated_at=occurred_at)
            remaining -= deduction

        movement = StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            from_location=stock_location,
            to_location=None,
            movement_type=StockMovementType.ADJUSTMENT,
            quantity=quantity * Decimal("-1"),
            reference_type="inventory_item",
            reference_id=str(inventory_item.id),
            actor_user_id=7,
            occurred_at=occurred_at,
            notes=reason,
            created_by_user_id=7,
            updated_by_user_id=7,
        )
        StockMovement.objects.filter(pk=movement.pk).update(created_at=occurred_at, updated_at=occurred_at)
