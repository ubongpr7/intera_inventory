from __future__ import annotations

import atexit
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from confluent_kafka import Consumer, Producer
from confluent_kafka.error import KafkaException

from subapps.kafka.config import get_kafka_settings

logger = logging.getLogger(__name__)

_producer: Producer | None = None


def get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer(get_kafka_settings().producer_config())
    return _producer


def build_consumer() -> Consumer:
    return Consumer(get_kafka_settings().consumer_config())


def flush_producer(timeout: float = 10.0) -> None:
    global _producer
    if _producer is None:
        return
    _producer.flush(timeout)


def _delivery_report(err, msg) -> None:
    if err is not None:
        logger.error(
            "Kafka delivery failed topic=%s partition=%s: %s",
            msg.topic(),
            msg.partition(),
            err,
        )
        return

    logger.debug(
        "Kafka message delivered topic=%s partition=%s offset=%s",
        msg.topic(),
        msg.partition(),
        msg.offset(),
    )


def _default_headers(headers: Iterable[tuple[str, str]] | None) -> list[tuple[str, str]]:
    normalized = list(headers or [])
    if not any(name.lower() == "content-type" for name, _ in normalized):
        normalized.append(("content-type", "application/json"))
    return normalized


def publish_event(
    topic: str,
    event_name: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
    headers: Iterable[tuple[str, str]] | None = None,
    event_id: str | None = None,
    event_version: int = 1,
    use_outbox: bool | None = None,
) -> dict[str, Any]:
    kafka_settings = get_kafka_settings()
    envelope = {
        "event_id": event_id or str(uuid.uuid4()),
        "event_name": event_name,
        "event_version": event_version,
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
        "source_service": kafka_settings.service_name,
        "payload": payload,
    }
    should_use_outbox = kafka_settings.use_outbox if use_outbox is None else use_outbox
    if should_use_outbox:
        from subapps.kafka.reliability import enqueue_outbox_event

        queued = enqueue_outbox_event(
            topic=topic,
            event_name=event_name,
            envelope=envelope,
            key=key,
            headers=headers,
        )
        if queued:
            return envelope

    produce_json_message(topic, envelope, key=key, headers=headers)
    return envelope


def produce_json_message(
    topic: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
    headers: Iterable[tuple[str, str]] | None = None,
) -> None:
    producer = get_producer()
    encoded_payload = json.dumps(payload, default=str).encode("utf-8")

    try:
        producer.produce(
            topic,
            value=encoded_payload,
            key=key,
            headers=_default_headers(headers),
            on_delivery=_delivery_report,
        )
    except KafkaException:
        logger.exception("Failed to enqueue Kafka message topic=%s", topic)
        raise

    producer.poll(0)


def decode_message_value(value: bytes | None) -> dict[str, Any]:
    if not value:
        return {}

    decoded = json.loads(value.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Kafka message payload must be a JSON object.")
    return decoded


def normalize_headers(headers: list[tuple[str, bytes | str | None]] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers or []:
        if value is None:
            normalized[key] = ""
        elif isinstance(value, bytes):
            normalized[key] = value.decode("utf-8")
        else:
            normalized[key] = str(value)
    return normalized


atexit.register(flush_producer)
