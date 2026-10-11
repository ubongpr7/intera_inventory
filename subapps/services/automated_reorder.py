from __future__ import annotations

import logging
import math
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from mainapps.inventory.models import AutomatedReorderDecision, InventoryItem, InventoryPlacement
from mainapps.inventory.models import InventoryItemStatus
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem, PurchaseOrderStatus
from mainapps.stock.models import StockBalance
from subapps.kafka.producers.platform_events import publish_workspace_notification
from subapps.services.location_scope import get_location_scope_ids

logger = logging.getLogger(__name__)


class AutomatedReorderService:
    """Evaluate and create approval-gated replenishment orders."""

    OPEN_PURCHASE_ORDER_STATUSES = {
        PurchaseOrderStatus.PENDING,
        PurchaseOrderStatus.APPROVED,
        PurchaseOrderStatus.ISSUED,
        PurchaseOrderStatus.RECEIVED,
    }

    @classmethod
    def evaluate(cls, *, profile_id: int | None = None) -> dict[str, int]:
        placements = InventoryPlacement.objects.filter(active=True).select_related(
            'inventory_item', 'structural_location'
        )
        if profile_id is not None:
            placements = placements.filter(profile_id=profile_id)

        result = {'evaluated': 0, 'created': 0, 'resolved': 0, 'skipped': 0}
        for placement in placements.iterator():
            result['evaluated'] += 1
            outcome = cls.evaluate_placement(placement)
            result[outcome] += 1
        return result

    @classmethod
    def evaluate_placement(cls, placement: InventoryPlacement) -> str:
        item = placement.inventory_item
        if item.status != InventoryItemStatus.ACTIVE or not item.purchaseable:
            return 'skipped'

        auto_reorder = (
            placement.automate_reorder_override
            if placement.automate_reorder_override is not None
            else bool(getattr(item, 'automate_reorder', False))
        )
        reorder_point = placement.reorder_point_override
        if reorder_point is None:
            reorder_point = item.reorder_point
        reorder_quantity = placement.reorder_quantity_override
        if reorder_quantity is None:
            reorder_quantity = item.reorder_quantity
        reorder_point = Decimal(str(reorder_point or 0))
        reorder_quantity = Decimal(str(reorder_quantity or 0))

        if not auto_reorder or reorder_point <= 0 or reorder_quantity <= 0:
            return 'skipped'

        scope_ids = get_location_scope_ids(
            profile_id=placement.profile_id,
            stock_location=placement.structural_location,
        ) or [placement.structural_location_id]
        quantity = StockBalance.objects.filter(
            profile_id=placement.profile_id,
            inventory_item=item,
            stock_location_id__in=scope_ids,
        ).aggregate(total=Sum('quantity_available')).get('total') or Decimal('0')
        quantity = Decimal(str(quantity))

        open_decision = AutomatedReorderDecision.objects.filter(
            profile_id=placement.profile_id,
            inventory_item=item,
            structural_location=placement.structural_location,
            status=AutomatedReorderDecision.Status.OPEN,
        ).select_related('purchase_order').first()
        if open_decision:
            order = open_decision.purchase_order
            if order and order.status in cls.OPEN_PURCHASE_ORDER_STATUSES:
                return 'skipped'
            open_decision.status = AutomatedReorderDecision.Status.RESOLVED
            open_decision.save(update_fields=['status', 'updated_at'])
            return 'resolved'

        if quantity > reorder_point:
            return 'skipped'

        supplier, last_price = cls._resolve_supplier_and_price(item=item, profile_id=placement.profile_id)
        if supplier is None:
            logger.warning(
                'Automatic reorder skipped: no supplier for item %s at structural location %s',
                item.id,
                placement.structural_location_id,
            )
            return 'skipped'

        quantity_to_order = max(1, math.ceil(reorder_quantity))
        trigger_date = timezone.now().date().isoformat()
        idempotency_key = (
            f'auto-reorder:{placement.profile_id}:{item.id}:{placement.structural_location_id}:{trigger_date}'
        )

        try:
            with transaction.atomic():
                decision = AutomatedReorderDecision.objects.create(
                    profile_id=placement.profile_id,
                    inventory_item=item,
                    structural_location=placement.structural_location,
                    status=AutomatedReorderDecision.Status.OPEN,
                    quantity_on_hand=quantity,
                    reorder_point=reorder_point,
                    reorder_quantity=reorder_quantity,
                    supplier_id_snapshot=supplier.id,
                    idempotency_key=idempotency_key,
                    metadata={'source': 'automated_reorder_worker'},
                )
                purchase_order = PurchaseOrder.objects.create(
                    profile_id=placement.profile_id,
                    supplier=supplier,
                    status=PurchaseOrderStatus.PENDING,
                    workflow_state='PENDING_APPROVAL',
                    approval_required=True,
                    description=f'Automatic reorder: {item.name_snapshot}',
                    notes=(
                        'System-curated replenishment. Approval is required before issue. '
                        f'Target structural location: {placement.structural_location.name}.'
                    ),
                )
                PurchaseOrderLineItem.objects.create(
                    purchase_order=purchase_order,
                    inventory_item=item,
                    structural_location=placement.structural_location,
                    quantity=quantity_to_order,
                    unit_price=last_price,
                    quantity_uom_code=item.stock_uom_code or item.default_uom_code or '',
                    stock_quantity=Decimal(quantity_to_order),
                    conversion_factor_snapshot=Decimal('1'),
                )
                decision.purchase_order = purchase_order
                decision.save(update_fields=['purchase_order', 'updated_at'])
        except IntegrityError:
            # Another worker won the unique open-decision race.
            return 'skipped'

        try:
            publish_workspace_notification(
                event_name='inventory.automated_reorder.created',
                workspace_id=str(placement.profile_id),
                category='stock_alert',
                title=f'Reorder approval required: {item.name_snapshot}',
                message=(
                    f'{item.name_snapshot} is at {quantity} units in {placement.structural_location.name}. '
                    f'A system-curated purchase order for {quantity_to_order} units is awaiting approval.'
                ),
                metadata={
                    'purchase_order_id': str(purchase_order.id),
                    'inventory_item_id': str(item.id),
                    'structural_location_id': str(placement.structural_location_id),
                    'quantity_on_hand': str(quantity),
                    'reorder_point': str(reorder_point),
                    'reorder_quantity': str(reorder_quantity),
                },
                action_url=f'/order/purchase/{purchase_order.id}',
                key=idempotency_key,
            )
        except Exception:
            logger.exception('Automatic reorder notification failed for %s', purchase_order.reference)
        return 'created'

    @staticmethod
    def _resolve_supplier_and_price(*, item: InventoryItem, profile_id: int):
        latest_line = (
            PurchaseOrderLineItem.objects.filter(
                purchase_order__profile_id=profile_id,
                inventory_item=item,
                purchase_order__supplier__is_supplier=True,
            )
            .select_related('purchase_order__supplier')
            .order_by('-purchase_order__created_at')
            .first()
        )
        if latest_line:
            return latest_line.purchase_order.supplier, latest_line.unit_price
        if item.default_supplier_id:
            return item.default_supplier, Decimal('0')
        return None, Decimal('0')
