from __future__ import annotations

from typing import Any, Iterable

from subapps.kafka.client import publish_event
from subapps.kafka.topics import AUDIT_EVENTS_TOPIC, NOTIFICATION_DISPATCH_TOPIC, NOTIFICATION_EVENTS_TOPIC


def _string(value: Any) -> str:
    return str(value or "").strip()


def _compact(mapping: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict):
            nested = _compact(value)
            if not nested:
                continue
            compacted[key] = nested
            continue
        compacted[key] = value
    return compacted


def build_audit_envelope(
    *,
    workspace_id: str,
    actor: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    summary: str,
    severity: str = "info",
    visibility_scope: str = "workspace",
    metadata: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    reference_number: str = "",
    correlation_id: str = "",
    request_id: str = "",
    feature_area: str = "",
) -> dict[str, Any]:
    return _compact(
        {
            "workspace_id": workspace_id,
            "actor": actor or {},
            "target": target or {},
            "summary": summary,
            "severity": severity,
            "visibility_scope": visibility_scope,
            "metadata": metadata or {},
            "changes": changes or {},
            "reference_number": reference_number,
            "correlation_id": correlation_id,
            "request_id": request_id,
            "feature_area": feature_area,
        }
    )


def publish_audit_fact(
    *,
    event_name: str,
    payload: dict[str, Any],
    workspace_id: str = "",
    actor: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    summary: str = "",
    severity: str = "info",
    visibility_scope: str = "workspace",
    metadata: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    reference_number: str = "",
    correlation_id: str = "",
    request_id: str = "",
    feature_area: str = "",
    key: str | None = None,
) -> dict[str, Any]:
    return publish_event(
        AUDIT_EVENTS_TOPIC,
        event_name,
        payload,
        key=key,
        envelope_overrides=build_audit_envelope(
            workspace_id=workspace_id,
            actor=actor,
            target=target,
            summary=summary,
            severity=severity,
            visibility_scope=visibility_scope,
            metadata=metadata,
            changes=changes,
            reference_number=reference_number,
            correlation_id=correlation_id,
            request_id=request_id,
            feature_area=feature_area,
        ),
    )


def publish_workspace_notification(
    *,
    event_name: str,
    workspace_id: str,
    category: str,
    title: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    action_url: str | None = None,
    user_ids: Iterable[Any] | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    recipients = [_string(user_id) for user_id in (user_ids or []) if _string(user_id)]
    return publish_event(
        NOTIFICATION_EVENTS_TOPIC,
        event_name,
        {
            "workspace_id": workspace_id,
            "category": category,
            "title": title,
            "message": message,
            "user_ids": recipients,
            "metadata": metadata or {},
            "action_url": action_url or "",
        },
        key=key,
    )


def publish_notification_dispatch(*, payload: dict[str, Any], key: str) -> dict[str, Any]:
    return publish_event(
        NOTIFICATION_DISPATCH_TOPIC,
        "notification.dispatch.requested",
        payload,
        key=key,
        use_outbox=True,
    )
