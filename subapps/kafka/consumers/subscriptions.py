from __future__ import annotations

from typing import Any

from mainapps.subscription_bootstrap.services import apply_subscription_activation
from subapps.services.subscription_entitlements import invalidate_subscription_decisions


def handle_workspace_subscription_event(envelope: dict[str, Any], **_: Any) -> bool:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("Workspace subscription payload must be a JSON object.")

    subscription = payload.get("subscription") or {}
    if not isinstance(subscription, dict):
        raise ValueError("Workspace subscription payload has an invalid subscription.")

    profile_id = payload.get("profile_id") or payload.get("workspace_id")
    subscription_id = subscription.get("id")
    event_id = envelope.get("event_id")
    if not profile_id or not subscription_id or not event_id:
        raise ValueError("Workspace subscription event requires profile, subscription, and event IDs.")

    apply_subscription_activation(
        profile_id=profile_id,
        subscription_id=subscription_id,
        activation_event_id=str(event_id),
        payload=payload,
    )
    application = str(
        subscription.get('application')
        or subscription.get('application_slug')
        or payload.get('application')
        or payload.get('application_slug')
        or 'intera-ims'
    ).strip()
    invalidate_subscription_decisions(profile_id=profile_id, application=application)
    return True
