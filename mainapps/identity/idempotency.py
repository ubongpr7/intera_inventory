"""Shared server-side idempotency primitives for mutating API requests."""

import hashlib
import json
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import IdempotencyRecord


def request_idempotency_key(request, *, required=False):
    value = str(
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or request.data.get("idempotency_key")
        or ""
    ).strip()
    if len(value) > 255:
        return None
    if required and not value:
        return None
    return value


def request_hash(request):
    payload = request.data
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        canonical = repr(payload)
    material = f"{request.method}:{request.path}:{canonical}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def raw_request_hash(request):
    material = f"{request.method}:{request.path}:".encode("utf-8") + (request.body or b"")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class IdempotencyClaim:
    record: IdempotencyRecord
    replay: Response | None = None


def claim_request(*, profile_id, operation, key, request_hash_value):
    """Claim a key or return a replay/conflict response.

    The unique constraint closes the concurrent-create race. Callers must
    complete the claim only after their domain transaction succeeds.
    """
    if not key:
        return IdempotencyClaim(record=None)
    try:
        with transaction.atomic():
            record, created = IdempotencyRecord.objects.get_or_create(
                profile_id=profile_id,
                operation=operation,
                key=key,
                defaults={"request_hash": request_hash_value},
            )
    except IntegrityError:
        record = IdempotencyRecord.objects.get(
            profile_id=profile_id, operation=operation, key=key
        )
        created = False

    if not created:
        if record.request_hash != request_hash_value:
            return IdempotencyClaim(
                record=record,
                replay=Response(
                    {"detail": "Idempotency-Key was reused with a different payload."},
                    status=status.HTTP_409_CONFLICT,
                ),
            )
        if record.status == IdempotencyRecord.STATUS_COMPLETED:
            return IdempotencyClaim(
                record=record,
                replay=Response(record.response_body or {}, status=record.response_status or 200),
            )
        if record.status == IdempotencyRecord.STATUS_FAILED:
            record.status = IdempotencyRecord.STATUS_PROCESSING
            record.response_status = None
            record.response_body = None
            record.completed_at = None
            record.save(update_fields=["status", "response_status", "response_body", "completed_at"])
            return IdempotencyClaim(record=record)
        return IdempotencyClaim(
            record=record,
            replay=Response(
                {"detail": "The same idempotent request is already being processed."},
                status=status.HTTP_409_CONFLICT,
            ),
        )
    return IdempotencyClaim(record=record)


def complete_request(record, response, *, resource_type="", resource_id=""):
    if record is None:
        return
    body = response.data
    if not isinstance(body, (dict, list, str, int, float, bool, type(None))):
        body = {"detail": str(body)}
    else:
        # DRF payloads may contain UUID/date/Decimal values that JSONField
        # cannot encode directly. Preserve the response shape while making it
        # durable and replayable.
        body = json.loads(json.dumps(body, default=str))
    IdempotencyRecord.objects.filter(pk=record.pk).update(
        status=IdempotencyRecord.STATUS_COMPLETED,
        response_status=response.status_code,
        response_body=body,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        completed_at=timezone.now(),
    )


def fail_request(record):
    if record is not None:
        IdempotencyRecord.objects.filter(pk=record.pk).update(
            status=IdempotencyRecord.STATUS_FAILED,
            completed_at=timezone.now(),
        )
