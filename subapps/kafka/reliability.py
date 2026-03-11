from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta
from typing import Any, Iterable

from django.db import DatabaseError, transaction
from django.utils import timezone

from subapps.kafka.config import get_kafka_settings

logger = logging.getLogger(__name__)


def _load_models():
    from mainapps.kafka_reliability.models import (
        KafkaConsumedEvent,
        KafkaConsumedEventStatus,
        KafkaDeadLetterEvent,
        KafkaDeadLetterStatus,
        KafkaOutboxEvent,
        KafkaOutboxStatus,
    )

    return {
        "KafkaConsumedEvent": KafkaConsumedEvent,
        "KafkaConsumedEventStatus": KafkaConsumedEventStatus,
        "KafkaDeadLetterEvent": KafkaDeadLetterEvent,
        "KafkaDeadLetterStatus": KafkaDeadLetterStatus,
        "KafkaOutboxEvent": KafkaOutboxEvent,
        "KafkaOutboxStatus": KafkaOutboxStatus,
    }


def _normalize_headers_for_storage(headers: Iterable[tuple[str, str]] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers or []:
        normalized[str(key)] = str(value)
    return normalized


def _headers_to_iterable(headers_json: dict[str, str] | None) -> list[tuple[str, str]]:
    return [(str(key), str(value)) for key, value in (headers_json or {}).items()]


def enqueue_outbox_event(
    *,
    topic: str,
    event_name: str,
    envelope: dict[str, Any],
    key: str | None = None,
    headers: Iterable[tuple[str, str]] | None = None,
) -> bool:
    models = _load_models()
    KafkaOutboxEvent = models["KafkaOutboxEvent"]
    KafkaOutboxStatus = models["KafkaOutboxStatus"]
    event_id = str(envelope.get("event_id") or uuid.uuid4())

    try:
        KafkaOutboxEvent.objects.update_or_create(
            event_id=event_id,
            defaults={
                "topic": topic,
                "event_name": event_name,
                "event_key": key or "",
                "source_service": str(envelope.get("source_service") or ""),
                "event_version": int(envelope.get("event_version") or 1),
                "message_json": envelope,
                "headers_json": _normalize_headers_for_storage(headers),
                "status": KafkaOutboxStatus.PENDING,
                "next_attempt_at": timezone.now(),
                "last_error": "",
            },
        )
        return True
    except DatabaseError:
        logger.exception("Failed to enqueue Kafka outbox event topic=%s event_id=%s", topic, event_id)
        return False


def publish_outbox_batch(*, batch_size: int | None = None) -> dict[str, int]:
    settings = get_kafka_settings()
    models = _load_models()
    KafkaOutboxEvent = models["KafkaOutboxEvent"]
    KafkaOutboxStatus = models["KafkaOutboxStatus"]
    now = timezone.now()
    stats = {"published": 0, "failed": 0}
    size = batch_size or settings.outbox_batch_size

    try:
        with transaction.atomic():
            events = list(
                KafkaOutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(
                    status__in=[KafkaOutboxStatus.PENDING, KafkaOutboxStatus.FAILED],
                    next_attempt_at__lte=now,
                )
                .order_by("created_at")[:size]
            )
            for event in events:
                event.status = KafkaOutboxStatus.IN_PROGRESS
                event.publish_attempts += 1
                event.save(update_fields=["status", "publish_attempts", "updated_at"])
    except DatabaseError:
        logger.exception("Failed to load Kafka outbox batch.")
        return stats

    if not events:
        return stats

    from subapps.kafka.client import produce_json_message

    for event in events:
        try:
            produce_json_message(
                event.topic,
                event.message_json,
                key=event.event_key or None,
                headers=_headers_to_iterable(event.headers_json),
            )
            event.status = KafkaOutboxStatus.PUBLISHED
            event.published_at = timezone.now()
            event.last_error = ""
            event.save(update_fields=["status", "published_at", "last_error", "updated_at"])
            stats["published"] += 1
        except Exception as exc:
            event.status = KafkaOutboxStatus.FAILED
            event.last_error = str(exc)
            event.next_attempt_at = timezone.now() + timedelta(seconds=settings.outbox_retry_delay_seconds)
            event.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
            stats["failed"] += 1
            logger.exception("Failed publishing Kafka outbox event topic=%s event_id=%s", event.topic, event.event_id)

    return stats


def run_outbox_publisher(
    *,
    run_duration: float | None = None,
    poll_interval: float | None = None,
    batch_size: int | None = None,
    run_once: bool = False,
) -> None:
    settings = get_kafka_settings()
    deadline = time.monotonic() + run_duration if run_duration else None
    interval = poll_interval if poll_interval is not None else settings.outbox_poll_interval_seconds

    while True:
        publish_outbox_batch(batch_size=batch_size)
        if run_once:
            return
        if deadline and time.monotonic() >= deadline:
            return
        time.sleep(interval)


def has_processed_event(event_id: str | None, *, consumer_group: str) -> bool:
    if not event_id:
        return False
    models = _load_models()
    KafkaConsumedEvent = models["KafkaConsumedEvent"]
    try:
        return KafkaConsumedEvent.objects.filter(event_id=str(event_id), consumer_group=consumer_group).exists()
    except DatabaseError:
        return False


def mark_event_processed(
    *,
    event_id: str | None,
    consumer_group: str,
    topic: str,
    envelope: dict[str, Any],
    status: str,
    error_message: str = "",
) -> None:
    if not event_id:
        return
    models = _load_models()
    KafkaConsumedEvent = models["KafkaConsumedEvent"]
    try:
        KafkaConsumedEvent.objects.update_or_create(
            event_id=str(event_id),
            consumer_group=consumer_group,
            defaults={
                "topic": topic,
                "event_name": str(envelope.get("event_name") or ""),
                "source_service": str(envelope.get("source_service") or ""),
                "status": status,
                "error_message": error_message,
                "processed_at": timezone.now(),
            },
        )
    except DatabaseError:
        logger.exception("Failed to persist Kafka consumed event event_id=%s group=%s", event_id, consumer_group)


def dead_letter_event(
    *,
    topic: str,
    consumer_group: str,
    envelope: dict[str, Any],
    headers: dict[str, str] | None,
    error_message: str,
) -> bool:
    settings = get_kafka_settings()
    models = _load_models()
    KafkaDeadLetterEvent = models["KafkaDeadLetterEvent"]
    KafkaDeadLetterStatus = models["KafkaDeadLetterStatus"]
    KafkaConsumedEventStatus = models["KafkaConsumedEventStatus"]

    event_id = str(envelope.get("event_id") or uuid.uuid4())
    dead_letter_topic = f"{topic}{settings.dlq_suffix}"
    dead_letter_envelope = {
        **envelope,
        "dead_letter": {
            "original_topic": topic,
            "consumer_group": consumer_group,
            "error_message": error_message,
            "failed_at": timezone.now().isoformat(),
        },
    }

    if settings.enable_dlq:
        try:
            from subapps.kafka.client import produce_json_message

            produce_json_message(
                dead_letter_topic,
                dead_letter_envelope,
                key=event_id,
                headers=_headers_to_iterable(headers),
            )
        except Exception:
            logger.exception("Failed to publish Kafka dead-letter event topic=%s", dead_letter_topic)
            if not settings.commit_failed_messages:
                return False

    try:
        KafkaDeadLetterEvent.objects.create(
            event_id=event_id,
            topic=topic,
            dead_letter_topic=dead_letter_topic if settings.enable_dlq else "",
            consumer_group=consumer_group,
            event_name=str(envelope.get("event_name") or ""),
            source_service=str(envelope.get("source_service") or ""),
            message_json=envelope,
            headers_json=headers or {},
            error_message=error_message,
            status=KafkaDeadLetterStatus.PENDING,
        )
    except DatabaseError:
        logger.exception("Failed to persist Kafka dead-letter event event_id=%s", event_id)

    mark_event_processed(
        event_id=event_id,
        consumer_group=consumer_group,
        topic=topic,
        envelope=envelope,
        status=KafkaConsumedEventStatus.DEAD_LETTERED,
        error_message=error_message,
    )
    return settings.commit_failed_messages


def replay_dead_letter_events(*, limit: int | None = None, event_id: str | None = None) -> int:
    models = _load_models()
    KafkaDeadLetterEvent = models["KafkaDeadLetterEvent"]
    KafkaDeadLetterStatus = models["KafkaDeadLetterStatus"]

    queryset = KafkaDeadLetterEvent.objects.filter(status=KafkaDeadLetterStatus.PENDING).order_by("failed_at")
    if event_id:
        queryset = queryset.filter(event_id=event_id)
    if limit:
        queryset = queryset[:limit]

    from subapps.kafka.client import produce_json_message

    replayed = 0
    for record in queryset:
        envelope = dict(record.message_json or {})
        envelope["replay_of_event_id"] = str(record.event_id)
        envelope["event_id"] = str(uuid.uuid4())
        envelope["event_timestamp"] = timezone.now().isoformat()
        produce_json_message(
            record.topic,
            envelope,
            key=envelope["event_id"],
            headers=_headers_to_iterable(record.headers_json),
        )
        record.status = KafkaDeadLetterStatus.REPLAYED
        record.replayed_at = timezone.now()
        record.save(update_fields=["status", "replayed_at", "updated_at"])
        replayed += 1
    return replayed
