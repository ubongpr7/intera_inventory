from __future__ import annotations

import logging
from uuid import UUID
from typing import Any

from django.db import transaction

from mainapps.identity.models import IdentityCompanyProfile, IdentityMembership, IdentityUser
from subapps.utils.request_context import invalidate_permission_cache

logger = logging.getLogger(__name__)


def _coerce_int(value: Any, field_name: str) -> int:
    if value in (None, ""):
        raise ValueError(f"{field_name} is required.")
    return int(value)


def _user_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    email = payload.get("email")
    if not email:
        raise ValueError("Identity user payload must include email.")
    return {
        "email": email,
        "full_name": payload.get("full_name", "") or "",
        "is_active": bool(payload.get("is_active", True)),
    }


def _profile_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    company_code = payload.get("company_code")
    if not company_code:
        raise ValueError("Identity company profile payload must include company_code.")
    headquarters_address = payload.get("headquarters_address")
    raw_address_id = payload.get("headquarters_address_id")
    try:
        headquarters_address_id = UUID(str(raw_address_id)) if raw_address_id else None
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid headquarters_address_id=%r for profile=%s", raw_address_id, payload.get("profile_id"))
        headquarters_address_id = None
    return {
        "company_code": company_code,
        "display_name": payload.get("display_name") or company_code,
        "logo_url": payload.get("logo_url") or "",
        "headquarters_address": headquarters_address if isinstance(headquarters_address, dict) else {},
        "headquarters_address_id": headquarters_address_id,
        "owner_user_id": payload.get("owner_user_id"),
        "is_active": bool(payload.get("is_active", True)),
    }


def _upsert_user(payload: dict[str, Any]) -> IdentityUser:
    user_id = _coerce_int(payload.get("user_id"), "user_id")
    user, _ = IdentityUser.objects.update_or_create(
        user_id=user_id,
        defaults=_user_defaults(payload),
    )
    return user


def _upsert_profile(payload: dict[str, Any]) -> IdentityCompanyProfile:
    profile_id = _coerce_int(payload.get("profile_id"), "profile_id")
    profile, _ = IdentityCompanyProfile.objects.update_or_create(
        profile_id=profile_id,
        defaults=_profile_defaults(payload),
    )
    return profile


def handle_identity_user_event(envelope: dict[str, Any], **_: Any) -> bool:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("Identity user payload must be a JSON object.")

    defaults = _user_defaults(payload)
    if envelope.get("event_name") == "identity.user.deleted":
        defaults["is_active"] = False

    IdentityUser.objects.update_or_create(
        user_id=_coerce_int(payload.get("user_id"), "user_id"),
        defaults=defaults,
    )
    return True


def handle_identity_company_profile_event(envelope: dict[str, Any], **_: Any) -> bool:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("Identity company profile payload must be a JSON object.")

    defaults = _profile_defaults(payload)
    if envelope.get("event_name") == "identity.company_profile.deleted":
        defaults["is_active"] = False

    IdentityCompanyProfile.objects.update_or_create(
        profile_id=_coerce_int(payload.get("profile_id"), "profile_id"),
        defaults=defaults,
    )
    return True


def handle_identity_membership_event(envelope: dict[str, Any], **_: Any) -> bool:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("Identity membership payload must be a JSON object.")

    event_name = envelope.get("event_name")
    permissions = payload.get("permissions") or payload.get("permissions_json") or []
    if not isinstance(permissions, list):
        permissions = list(permissions)

    user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else {
        "user_id": payload.get("user_id"),
        "email": payload.get("user_email"),
        "full_name": payload.get("user_full_name", ""),
        "is_active": payload.get("user_is_active", True),
    }
    profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else {
        "profile_id": payload.get("profile_id"),
        "company_code": payload.get("company_code"),
        "display_name": payload.get("profile_display_name"),
        "owner_user_id": payload.get("owner_user_id"),
        "is_active": payload.get("profile_is_active", True),
    }

    with transaction.atomic():
        user = _upsert_user(user_payload)
        profile = _upsert_profile(profile_payload)
        membership, _ = IdentityMembership.objects.update_or_create(
            profile=profile,
            user=user,
            defaults={
                "role": payload.get("role", ""),
                "permissions_json": permissions,
                "is_active": event_name != "identity.membership.deleted" and bool(payload.get("is_active", True)),
            },
        )
        logger.debug(
            "Synced identity membership profile=%s user=%s active=%s",
            membership.profile_id,
            membership.user_id,
            membership.is_active,
        )
    return True


def handle_identity_permission_context_event(envelope: dict[str, Any], **_: Any) -> bool:
    if envelope.get("event_name") != "identity.permission_context.invalidated":
        return True
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        return True
    for user_id in payload.get("user_ids") or []:
        invalidate_permission_cache(
            user_id=str(user_id),
            profile_id=str(payload.get("profile_id") or ""),
            platform=str(payload.get("platform") or ""),
        )
    return True
