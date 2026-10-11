from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from mainapps.inventory.models import InventoryItem, InventoryItemStatus, OperationalAutomationAlert
from mainapps.orders.models import PurchaseOrder, PurchaseOrderStatus
from mainapps.stock.models import StockBalance, StockLot, StockLotStatus
from subapps.kafka.producers.inventory import publish_inventory_availability_upserted
from subapps.kafka.producers.platform_events import publish_workspace_notification
from subapps.services.automated_reorder import AutomatedReorderService

logger = logging.getLogger(__name__)


class OperationalAutomationService:
    """Run supported operational automations and repair derived projections."""

    EXPIRY_WINDOWS = (30, 14, 7)
    TERMINAL_PO_STATUSES = {
        PurchaseOrderStatus.COMPLETED,
        PurchaseOrderStatus.CANCELLED,
        PurchaseOrderStatus.REJECTED,
        PurchaseOrderStatus.LOST,
        PurchaseOrderStatus.RETURNED,
    }

    @classmethod
    def evaluate(cls, *, profile_id: int | None = None) -> dict[str, int]:
        result = {
            'reorder_evaluated': 0,
            'reorder_created': 0,
            'reorder_resolved': 0,
            'reorder_skipped': 0,
            'expiry_alerts_created': 0,
            'po_alerts_created': 0,
            'stock_alerts_created': 0,
            'availability_reconciled': 0,
            'availability_failed': 0,
        }
        reorder = AutomatedReorderService.evaluate(profile_id=profile_id)
        result.update({f'reorder_{key}': value for key, value in reorder.items()})
        result['expiry_alerts_created'] = cls._evaluate_expiry(profile_id=profile_id)
        result['po_alerts_created'] = cls._evaluate_purchase_orders(profile_id=profile_id)
        result['stock_alerts_created'] = cls._evaluate_stock_integrity(profile_id=profile_id)
        availability = cls._reconcile_inventory_availability(profile_id=profile_id)
        result['availability_reconciled'] = availability['reconciled']
        result['availability_failed'] = availability['failed']
        return result

    @classmethod
    def _reconcile_inventory_availability(cls, *, profile_id: int | None) -> dict[str, int]:
        """Republish active inventory snapshots so POS projections self-heal.

        Availability is a derived projection consumed by POS. Inventory mutations
        publish these events immediately, while this periodic replay repairs events
        missed during broker, consumer, or deployment interruptions.
        """
        items = InventoryItem.objects.filter(status=InventoryItemStatus.ACTIVE).order_by('updated_at')
        if profile_id is not None:
            items = items.filter(profile_id=profile_id)

        reconciled = 0
        failed = 0
        for item in items.iterator(chunk_size=100):
            try:
                envelope = publish_inventory_availability_upserted(inventory_item_id=item.id)
                if envelope is None:
                    failed += 1
                    logger.warning(
                        'Inventory availability reconciliation skipped item=%s profile=%s.',
                        item.id,
                        item.profile_id,
                    )
                else:
                    reconciled += 1
            except Exception:
                failed += 1
                logger.exception(
                    'Inventory availability reconciliation failed item=%s profile=%s.',
                    item.id,
                    item.profile_id,
                )
        return {'reconciled': reconciled, 'failed': failed}

    @classmethod
    def _evaluate_expiry(cls, *, profile_id: int | None) -> int:
        today = timezone.now().date()
        lots = StockLot.objects.filter(
            expiry_date__isnull=False,
            remaining_quantity__gt=0,
        ).exclude(status__in=[StockLotStatus.DEPLETED, StockLotStatus.CLOSED])
        if profile_id is not None:
            lots = lots.filter(profile_id=profile_id)

        created = 0
        for lot in lots.select_related('inventory_item').iterator():
            days_until_expiry = (lot.expiry_date - today).days
            window = next((days for days in cls.EXPIRY_WINDOWS if days_until_expiry <= days), None)
            if window is None:
                continue
            dedupe_key = f'expiry:{lot.profile_id}:{lot.id}:{window}'
            alert, was_created = cls._create_alert(
                kind=OperationalAutomationAlert.Kind.EXPIRY,
                profile_id=lot.profile_id,
                dedupe_key=dedupe_key,
                severity='critical' if days_until_expiry <= 7 else 'warning',
                inventory_item=lot.inventory_item,
                stock_lot=lot,
                metadata={
                    'expiry_date': lot.expiry_date.isoformat(),
                    'days_until_expiry': days_until_expiry,
                    'window_days': window,
                    'remaining_quantity': str(lot.remaining_quantity),
                },
                title=f'Expiry alert: {lot.inventory_item.name_snapshot}',
                message=(
                    f'{lot.inventory_item.name_snapshot} has {lot.remaining_quantity} units '
                    f'remaining and expires in {days_until_expiry} days.'
                ),
                action_url=f'/inventory?tab=inventories&inventory_item={lot.inventory_item_id}',
            )
            created += int(was_created)
        return created

    @classmethod
    def _evaluate_purchase_orders(cls, *, profile_id: int | None) -> int:
        today = timezone.now().date()
        orders = PurchaseOrder.objects.filter(status__in=[
            PurchaseOrderStatus.PENDING,
            PurchaseOrderStatus.APPROVED,
            PurchaseOrderStatus.ISSUED,
            PurchaseOrderStatus.RECEIVED,
            PurchaseOrderStatus.OVERDUE,
        ]).prefetch_related('line_items')
        if profile_id is not None:
            orders = orders.filter(profile_id=profile_id)

        created = 0
        for order in orders.iterator(chunk_size=100):
            if order.status == PurchaseOrderStatus.PENDING and order.created_at.date() <= today - timedelta(days=1):
                _, was_created = cls._create_alert(
                    kind=OperationalAutomationAlert.Kind.PO_PENDING,
                    profile_id=order.profile_id,
                    dedupe_key=f'po-pending:{order.profile_id}:{order.id}',
                    severity='warning',
                    purchase_order=order,
                    metadata={'reference': order.reference, 'age_days': (today - order.created_at.date()).days},
                    title=f'Purchase order awaiting approval: {order.reference}',
                    message=f'{order.reference} has been awaiting approval since {order.created_at.date().isoformat()}.',
                    action_url=f'/order/purchase/{order.id}',
                )
                created += int(was_created)

            if order.delivery_date and order.delivery_date < today and order.status not in cls.TERMINAL_PO_STATUSES:
                _, was_created = cls._create_alert(
                    kind=OperationalAutomationAlert.Kind.PO_OVERDUE,
                    profile_id=order.profile_id,
                    dedupe_key=f'po-overdue:{order.profile_id}:{order.id}:{order.delivery_date.isoformat()}',
                    severity='critical',
                    purchase_order=order,
                    metadata={'delivery_date': order.delivery_date.isoformat()},
                    title=f'Purchase order overdue: {order.reference}',
                    message=f'{order.reference} was expected on {order.delivery_date.isoformat()} and is not complete.',
                    action_url=f'/order/purchase/{order.id}',
                )
                created += int(was_created)

            lines = list(order.line_items.all())
            if lines and any(line.quantity_received > 0 and not line.fully_received for line in lines):
                _, was_created = cls._create_alert(
                    kind=OperationalAutomationAlert.Kind.PO_PARTIAL,
                    profile_id=order.profile_id,
                    dedupe_key=f'po-partial:{order.profile_id}:{order.id}',
                    severity='warning',
                    purchase_order=order,
                    metadata={'reference': order.reference},
                    title=f'Partial receipt: {order.reference}',
                    message=f'{order.reference} has received goods but still has outstanding quantities.',
                    action_url=f'/order/purchase/{order.id}',
                )
                created += int(was_created)
        return created

    @classmethod
    def _evaluate_stock_integrity(cls, *, profile_id: int | None) -> int:
        balances = StockBalance.objects.select_related('inventory_item', 'stock_location')
        if profile_id is not None:
            balances = balances.filter(profile_id=profile_id)
        created = 0
        for balance in balances.filter(
            Q(quantity_available__lt=0) |
            ~Q(quantity_available=F('quantity_on_hand') - F('quantity_reserved'))
        ).iterator():
            dedupe_key = f'stock-integrity:{balance.profile_id}:{balance.id}:{balance.updated_at.isoformat()}'
            _, was_created = cls._create_alert(
                kind=OperationalAutomationAlert.Kind.STOCK_INTEGRITY,
                profile_id=balance.profile_id,
                dedupe_key=dedupe_key,
                severity='critical',
                inventory_item=balance.inventory_item,
                stock_balance=balance,
                structural_location=balance.stock_location if balance.stock_location.structural else None,
                metadata={
                    'quantity_on_hand': str(balance.quantity_on_hand),
                    'quantity_reserved': str(balance.quantity_reserved),
                    'quantity_available': str(balance.quantity_available),
                },
                title=f'Stock integrity exception: {balance.inventory_item.name_snapshot}',
                message=f'Stock balance at {balance.stock_location.name} requires review.',
                action_url='/inventory?tab=inventories',
            )
            created += int(was_created)
        return created

    @staticmethod
    def _create_alert(*, kind, profile_id, dedupe_key, severity, title, message, action_url, metadata, **relations):
        alert, created = OperationalAutomationAlert.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                'profile_id': profile_id,
                'kind': kind,
                'severity': severity,
                'metadata': metadata,
                **relations,
            },
        )
        if not created or alert.last_notified_at is not None:
            return alert, False
        try:
            publish_workspace_notification(
                event_name=f'inventory.automation.{kind}.created',
                workspace_id=str(profile_id),
                category='stock_alert',
                title=title,
                message=message,
                metadata={'alert_id': str(alert.id), **metadata},
                action_url=action_url,
                key=dedupe_key,
            )
            alert.last_notified_at = timezone.now()
            alert.save(update_fields=['last_notified_at', 'updated_at'])
        except Exception:
            logger.exception('Operational automation notification failed for %s', dedupe_key)
        return alert, True
