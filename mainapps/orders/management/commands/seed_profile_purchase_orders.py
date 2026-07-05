from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from mainapps.company.models import Company
from mainapps.inventory.models import InventoryItem
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem
from mainapps.stock.models import StockBalance


SEED_MARKER = "[seeded_by_codex:profile_po_seed_v1]"
USD_TO_NGN = Decimal("1373.10")
MAX_PO_VALUE_NGN = Decimal("10000") * USD_TO_NGN
LINE_DISCOUNT_RATE = Decimal("0.50")
ZERO = Decimal("0")


@dataclass(frozen=True)
class SeedSupplier:
    name: str
    website: str
    email: str
    short_address: str


@dataclass(frozen=True)
class SeedLine:
    inventory_item_id: str
    quantity: int
    unit_price: Decimal
    source_label: str
    source_url: str
    source_market_price: Decimal


@dataclass(frozen=True)
class SeedPurchaseOrder:
    name: str
    supplier_name: str
    description: str
    budget_code: str
    department: str
    lines: tuple[SeedLine, ...]


SUPPLIERS: tuple[SeedSupplier, ...] = (
    SeedSupplier(
        name="Tech Essentials Distribution Ltd",
        website="https://www.jumia.com.ng/slp/red-apple-watch-bands",
        email="procurement+tech@drabtech.local",
        short_address="Computer Village Annex, Ikeja, Lagos",
    ),
    SeedSupplier(
        name="StyleRack Apparel Wholesale Ltd",
        website="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
        email="procurement+apparel@drabtech.local",
        short_address="Trade Fair Complex, Badagry Expressway, Lagos",
    ),
)


PURCHASE_ORDERS: tuple[SeedPurchaseOrder, ...] = (
    SeedPurchaseOrder(
        name="Nivea body care restock",
        supplier_name="Multipro Consumer Products Ltd",
        description="Restock core Nivea lotions and cream lines for zero-stock shelf positions.",
        budget_code="BEAUTY-NIVEA-Q2",
        department="Beauty & Personal Care",
        lines=(
            SeedLine(
                inventory_item_id="9d705ab3-91dc-47c4-a475-25e9ae4f6684",
                quantity=900,
                unit_price=Decimal("4090.50"),
                source_label="Jumia NG - NIVEA Radiant & Beauty Advanced Care Body Lotion 400ml",
                source_url="https://www.jumia.com.ng/mlp-nivea-store/",
                source_market_price=Decimal("4545.00"),
            ),
            SeedLine(
                inventory_item_id="4d61aa17-0d30-43eb-9fe5-c208bbb79e9a",
                quantity=700,
                unit_price=Decimal("5737.50"),
                source_label="Jumia NG - NIVEA Men Crème 150ml pack of 2",
                source_url="https://www.jumia.com.ng/mlp-nivea-store/",
                source_market_price=Decimal("6375.00"),
            ),
            SeedLine(
                inventory_item_id="39b759ad-deba-48ba-bf35-c2902323f727",
                quantity=900,
                unit_price=Decimal("3042.00"),
                source_label="Jumia NG - NIVEA MEN Revitalizing Body Lotion 400ml",
                source_url="https://www.jumia.com.ng/mlp-nivea-store/",
                source_market_price=Decimal("3380.00"),
            ),
        ),
    ),
    SeedPurchaseOrder(
        name="Sure deodorant replenishment",
        supplier_name="Multipro Consumer Products Ltd",
        description="Replenish Sure deodorant and anti-perspirant variants currently out of stock.",
        budget_code="BEAUTY-SURE-Q2",
        department="Beauty & Personal Care",
        lines=(
            SeedLine(
                inventory_item_id="66d6b2c3-2f86-4a51-a05d-ca9ff4fdf2d0",
                quantity=300,
                unit_price=Decimal("6284.70"),
                source_label="Jumia NG - Sure Body Spray 250ml",
                source_url="https://www.jumia.com.ng/mlp-sure-body-spray/",
                source_market_price=Decimal("6983.00"),
            ),
            SeedLine(
                inventory_item_id="964bb211-b241-4df3-8d6a-871597e78151",
                quantity=300,
                unit_price=Decimal("5849.10"),
                source_label="Jumia NG - Sure Men Quantum Dry aerosol",
                source_url="https://www.jumia.com.ng/mlp-sure-body-spray/",
                source_market_price=Decimal("6499.00"),
            ),
            SeedLine(
                inventory_item_id="aca4e2ef-2004-4cae-8ebe-b60a62d7c105",
                quantity=300,
                unit_price=Decimal("6284.70"),
                source_label="Jumia NG - Sure deodorant body spray range",
                source_url="https://www.jumia.com.ng/mlp-sure-body-spray/",
                source_market_price=Decimal("6983.00"),
            ),
            SeedLine(
                inventory_item_id="dcb545c5-a223-431e-b78c-ee8b2af9a043",
                quantity=300,
                unit_price=Decimal("6284.70"),
                source_label="Jumia NG - Sure deodorant body spray range",
                source_url="https://www.jumia.com.ng/mlp-sure-body-spray/",
                source_market_price=Decimal("6983.00"),
            ),
            SeedLine(
                inventory_item_id="411ccfd5-e438-48af-aebf-1ae5f86be9c3",
                quantity=300,
                unit_price=Decimal("6284.70"),
                source_label="Jumia NG - Sure deodorant body spray range",
                source_url="https://www.jumia.com.ng/mlp-sure-body-spray/",
                source_market_price=Decimal("6983.00"),
            ),
            SeedLine(
                inventory_item_id="856cc3fe-e521-432a-8bc2-1ec3d308653a",
                quantity=300,
                unit_price=Decimal("5849.10"),
                source_label="Jumia NG - Sure Men Quantum Dry aerosol",
                source_url="https://www.jumia.com.ng/mlp-sure-body-spray/",
                source_market_price=Decimal("6499.00"),
            ),
        ),
    ),
    SeedPurchaseOrder(
        name="Premium fragrance assortment",
        supplier_name="Satnam Investment Nigeria Ltd",
        description="Restock premium 100ml eau de parfum lines with internet-anchored wholesale pricing.",
        budget_code="FRAGRANCE-PREMIUM-Q2",
        department="Beauty & Fragrance",
        lines=(
            SeedLine(
                inventory_item_id="f775ecee-d885-4ee2-85ae-a9a123c985a0",
                quantity=100,
                unit_price=Decimal("41220.00"),
                source_label="Afnan 9PM 100ml category anchor",
                source_url="https://www.jumia.com.ng/fragrances-allgenders/afnan/",
                source_market_price=Decimal("45800.00"),
            ),
            SeedLine(
                inventory_item_id="96f60903-732d-4f30-a243-236db57c9ccf",
                quantity=100,
                unit_price=Decimal("30969.00"),
                source_label="Afnan 9PM Rebel 100ml",
                source_url="https://www.jumia.com.ng/fragrances-allgenders/afnan/",
                source_market_price=Decimal("34410.00"),
            ),
        ),
    ),
    SeedPurchaseOrder(
        name="Value fragrance refresh",
        supplier_name="Satnam Investment Nigeria Ltd",
        description="Seed affordable fragrance stock for lower-ticket shelf coverage.",
        budget_code="FRAGRANCE-VALUE-Q2",
        department="Beauty & Fragrance",
        lines=(
            SeedLine(
                inventory_item_id="d4414ee9-f46c-4370-9a64-68bce9dae623",
                quantity=800,
                unit_price=Decimal("8369.10"),
                source_label="Jumia NG - generic 50ml perfume category anchor",
                source_url="https://www.jumia.com.ng/slp/prada-ocean-perfume-for-women/",
                source_market_price=Decimal("9299.00"),
            ),
        ),
    ),
    SeedPurchaseOrder(
        name="Polo apparel batch A",
        supplier_name="StyleRack Apparel Wholesale Ltd",
        description="Restock the first half of zero-stock knitted and short-sleeve polo shirt variants.",
        budget_code="APPAREL-POLO-A-Q2",
        department="Fashion",
        lines=(
            SeedLine(
                inventory_item_id="68083101-d25f-4613-8fd4-7e8da0ca7355",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
            SeedLine(
                inventory_item_id="688bc113-b93d-495c-9f23-1e67db9cd21b",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
            SeedLine(
                inventory_item_id="69270a81-7edb-44ed-903a-4dfd7ff28fe0",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
            SeedLine(
                inventory_item_id="bc426d19-b1cb-49cd-9b22-f7bda57356d6",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
        ),
    ),
    SeedPurchaseOrder(
        name="Polo apparel batch B",
        supplier_name="StyleRack Apparel Wholesale Ltd",
        description="Restock the second half of zero-stock knitted and short-sleeve polo shirt variants.",
        budget_code="APPAREL-POLO-B-Q2",
        department="Fashion",
        lines=(
            SeedLine(
                inventory_item_id="a6f3376b-c537-4d8d-8c1a-b7aad6285bb8",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
            SeedLine(
                inventory_item_id="0b6b75d5-41df-48e9-b9a7-cc01a397b46f",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
            SeedLine(
                inventory_item_id="312fb393-8c09-4e65-8054-c60e293cfbaf",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
            SeedLine(
                inventory_item_id="3331da4f-edc3-4479-ba82-b445b6232ca1",
                quantity=100,
                unit_price=Decimal("28800.00"),
                source_label="Next Nigeria - regular fit short sleeve pique polo shirt",
                source_url="https://www.nextdirect.com/ng/en/shop/mens/clothing/tops/polo-shirts",
                source_market_price=Decimal("32000.00"),
            ),
        ),
    ),
    SeedPurchaseOrder(
        name="Tech strap accessory restock",
        supplier_name="Tech Essentials Distribution Ltd",
        description="Restock smart watch sport band accessory line for electronics merchandising.",
        budget_code="TECH-ACCESSORY-Q2",
        department="Electronics",
        lines=(
            SeedLine(
                inventory_item_id="006f0b96-2731-46a9-88b9-2d6afb1d17d3",
                quantity=1200,
                unit_price=Decimal("3600.00"),
                source_label="Jumia NG - replacement smart watch strap",
                source_url="https://www.jumia.com.ng/slp/red-apple-watch-bands/",
                source_market_price=Decimal("4000.00"),
            ),
        ),
    ),
)


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Seed live purchase orders for a profile using curated internet price anchors."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, default=1)
        parser.add_argument(
            "--replace-generated",
            action="store_true",
            help="Delete previously generated seeded purchase orders before recreating them.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and print the purchase orders without writing to the database.",
        )

    def handle(self, *args, **options):
        profile_id = options["profile_id"]
        replace_generated = options["replace_generated"]
        dry_run = options["dry_run"]

        if replace_generated and not dry_run:
            self._delete_prior_seeded_purchase_orders(profile_id=profile_id)

        suppliers = self._ensure_suppliers(profile_id=profile_id, dry_run=dry_run)
        purchase_orders = self._build_purchase_orders(profile_id=profile_id, suppliers=suppliers)

        self._validate_purchase_orders(purchase_orders)

        if dry_run:
            self._print_summary(purchase_orders, dry_run=True)
            return

        created_purchase_orders = self._persist_purchase_orders(
            profile_id=profile_id,
            purchase_orders=purchase_orders,
        )
        self._print_summary(created_purchase_orders, dry_run=False)

    def _delete_prior_seeded_purchase_orders(self, *, profile_id: int) -> None:
        queryset = PurchaseOrder.objects.filter(profile_id=profile_id, notes__icontains=SEED_MARKER)
        deleted = queryset.count()
        queryset.delete()
        self.stdout.write(self.style.WARNING(f"Deleted {deleted} previously seeded purchase order(s)."))

    def _ensure_suppliers(self, *, profile_id: int, dry_run: bool) -> dict[str, Company]:
        suppliers: dict[str, Company] = {
            supplier.name: supplier
            for supplier in Company.objects.filter(profile_id=profile_id, is_supplier=True)
        }
        for supplier_seed in SUPPLIERS:
            if supplier_seed.name in suppliers:
                continue
            if dry_run:
                suppliers[supplier_seed.name] = Company(
                    profile_id=profile_id,
                    profile=str(profile_id),
                    name=supplier_seed.name,
                    is_supplier=True,
                    currency="NGN",
                )
                continue
            supplier, _ = Company.objects.update_or_create(
                profile_id=profile_id,
                name=supplier_seed.name,
                defaults={
                    "profile": str(profile_id),
                    "description": "Seeded supplier for purchase-order demo coverage.",
                    "website": supplier_seed.website,
                    "link": supplier_seed.website,
                    "email": supplier_seed.email,
                    "short_address": supplier_seed.short_address,
                    "is_supplier": True,
                    "currency": "NGN",
                },
            )
            suppliers[supplier.name] = supplier
        return suppliers

    def _build_purchase_orders(
        self,
        *,
        profile_id: int,
        suppliers: dict[str, Company],
    ) -> list[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]]:
        items = {
            str(item.id): item
            for item in InventoryItem.objects.filter(profile_id=profile_id)
        }
        zero_stock_ids = {
            str(item_id)
            for item_id in
            InventoryItem.objects.filter(profile_id=profile_id)
            .annotate(
                total_on_hand=Coalesce(
                    Sum("stock_balances__quantity_on_hand"),
                    Value(Decimal("0.00000")),
                    output_field=DecimalField(max_digits=15, decimal_places=5),
                )
            )
            .filter(total_on_hand=Decimal("0.00000"))
            .values_list("id", flat=True)
        }
        if len(zero_stock_ids) != 21:
            self.stdout.write(
                self.style.WARNING(
                    f"Expected 21 zero-stock items for profile {profile_id}, found {len(zero_stock_ids)}. Proceeding with live dataset."
                )
            )

        covered_ids: set[str] = set()
        now = timezone.now()
        built: list[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]] = []

        for index, seed_po in enumerate(PURCHASE_ORDERS, start=1):
            supplier = suppliers.get(seed_po.supplier_name)
            if supplier is None:
                raise CommandError(f"Supplier '{seed_po.supplier_name}' does not exist for profile {profile_id}.")

            po = PurchaseOrder(
                profile_id=profile_id,
                profile=str(profile_id),
                supplier=supplier,
                order_currency="NGN",
                status="issued",
                workflow_state="SENT_TO_SUPPLIER",
                issue_date=now,
                delivery_date=now.date(),
                description=seed_po.description,
                notes=(
                    f"{SEED_MARKER}\n"
                    f"PO seed family: {seed_po.name}\n"
                    f"Pricing approach: market anchors reduced by 10%.\n"
                    f"Line discount rate: {LINE_DISCOUNT_RATE}%.\n"
                    f"Per-PO cap benchmark: approx $10,000 at 1 USD ≈ ₦{USD_TO_NGN}."
                ),
                budget_code=seed_po.budget_code,
                department=seed_po.department,
                supplier_reference=f"SEED-{profile_id}-{index:02d}",
            )

            line_items: list[PurchaseOrderLineItem] = []
            for seed_line in seed_po.lines:
                item = items.get(seed_line.inventory_item_id)
                if item is None:
                    raise CommandError(f"Inventory item {seed_line.inventory_item_id} was not found.")
                if str(item.id) not in zero_stock_ids:
                    raise CommandError(
                        f"Inventory item {item.id} ('{item.name_snapshot}') is not currently zero-stock and was not expected in a seeded PO."
                    )
                covered_ids.add(str(item.id))
                line_items.append(
                    PurchaseOrderLineItem(
                        inventory_item=item,
                        quantity=seed_line.quantity,
                        unit_price=seed_line.unit_price,
                        discount_rate=LINE_DISCOUNT_RATE,
                        tax_rate=ZERO,
                        description=(
                            f"Internet price anchor: {seed_line.source_label}. "
                            f"Observed market price ₦{_money(seed_line.source_market_price)}; "
                            f"seeded PO unit price is 10% lower. "
                            f"Source: {seed_line.source_url}"
                        ),
                    )
                )

            built.append((po, line_items))

        missing_ids = sorted(zero_stock_ids - covered_ids)
        if missing_ids:
            missing_names = list(
                InventoryItem.objects.filter(id__in=missing_ids).values_list("name_snapshot", flat=True)
            )
            raise CommandError(f"Zero-stock items left unassigned to purchase orders: {missing_names}")

        return built

    def _validate_purchase_orders(
        self,
        purchase_orders: Iterable[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]],
    ) -> None:
        for po, lines in purchase_orders:
            total_quantity = sum(line.quantity for line in lines)
            if total_quantity > 5000:
                raise CommandError(f"PO '{po.description}' exceeds the 5,000 unit cap with {total_quantity} units.")

            po_total = ZERO
            for line in lines:
                line.full_clean(exclude=["purchase_order"])
                po_total += line.total_price
            if po_total > MAX_PO_VALUE_NGN:
                raise CommandError(
                    f"PO '{po.description}' exceeds the approximate $10,000 cap: ₦{_money(po_total)} > ₦{_money(MAX_PO_VALUE_NGN)}."
                )

    @transaction.atomic
    def _persist_purchase_orders(
        self,
        *,
        profile_id: int,
        purchase_orders: list[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]],
    ) -> list[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]]:
        persisted: list[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]] = []
        for po, lines in purchase_orders:
            po.save()
            for line in lines:
                line.purchase_order = po
                line.save()
                inventory_item = line.inventory_item
                if inventory_item.default_supplier_id is None:
                    inventory_item.default_supplier = po.supplier
                    inventory_item.save(update_fields=["default_supplier", "updated_at"])
            persisted.append((po, lines))
        return persisted

    def _print_summary(
        self,
        purchase_orders: Iterable[tuple[PurchaseOrder, list[PurchaseOrderLineItem]]],
        *,
        dry_run: bool,
    ) -> None:
        mode = "DRY RUN" if dry_run else "CREATED"
        for po, lines in purchase_orders:
            total_quantity = sum(line.quantity for line in lines)
            po_total = sum((line.total_price for line in lines), ZERO)
            reference = po.reference or "(pending reference)"
            self.stdout.write(
                f"{mode}|{reference}|supplier={po.supplier.name if po.supplier else 'N/A'}|"
                f"lines={len(lines)}|qty={total_quantity}|total_ngn={_money(po_total)}"
            )
