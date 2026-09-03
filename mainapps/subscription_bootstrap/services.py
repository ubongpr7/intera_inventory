from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from django.db import transaction

from .models import InventoryBootstrap, InventoryBootstrapStatus

logger = logging.getLogger(__name__)

INVENTORY_APPLICATION_SLUG = "intera-ims"
INVENTORY_BOOTSTRAP_VERSION = "inventory-bootstrap-v1"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def paid_active_inventory_subscription(payload: dict[str, Any]) -> tuple[bool, str]:
    """Validate the trusted Subscription snapshot before any bootstrap work."""
    if str(payload.get("application", "")).strip().lower() != INVENTORY_APPLICATION_SLUG:
        return False, "application_not_inventory"

    subscription = payload.get("subscription")
    if not isinstance(subscription, dict):
        return False, "subscription_missing"
    if str(subscription.get("status", "")).upper() != "ACTIVE":
        return False, "subscription_not_active"
    if subscription.get("billing_authorized") is not True:
        return False, "subscription_not_paid"
    if str(subscription.get("current_payment_status", "")).upper() != "COMPLETED":
        return False, "payment_not_completed"

    access_until = _parse_datetime(subscription.get("access_until") or subscription.get("end_date"))
    if access_until is not None and access_until <= datetime.now(timezone.utc):
        return False, "subscription_expired"
    return True, "eligible"


@transaction.atomic
def apply_subscription_activation(
    *,
    profile_id: Any,
    subscription_id: Any,
    activation_event_id: str,
    payload: dict[str, Any],
) -> InventoryBootstrap | None:
    """Record an eligible activation exactly once.

    Tenant-specific products, locations, POS configuration, and permissions are
    intentionally not created here. They must be added behind this same gate in
    a later, explicitly versioned seed contract.
    """
    eligible, reason = paid_active_inventory_subscription(payload)
    if not eligible:
        logger.info(
            "Inventory bootstrap rejected profile=%s reason=%s",
            profile_id,
            reason,
        )
        return None

    receipt, created = InventoryBootstrap.objects.get_or_create(
        profile_id=int(profile_id),
        application_slug=INVENTORY_APPLICATION_SLUG,
        defaults={
            "subscription_id": str(subscription_id),
            "activation_event_id": str(activation_event_id),
            "bootstrap_version": INVENTORY_BOOTSTRAP_VERSION,
            "status": InventoryBootstrapStatus.COMPLETED,
            "details": {"eligibility": reason},
        },
    )
    if not created:
        logger.info(
            "Inventory bootstrap already recorded profile=%s event=%s",
            profile_id,
            activation_event_id,
        )
    return receipt
