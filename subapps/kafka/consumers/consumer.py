from __future__ import annotations

import os
import logging
import signal
import time
from typing import Any

from confluent_kafka import KafkaError
from django.db import close_old_connections

from subapps.kafka.client import build_consumer, decode_message_value, normalize_headers
from subapps.kafka.config import get_kafka_settings
from subapps.kafka.consumers.handlers import EVENT_HANDLERS
from subapps.kafka.reliability import dead_letter_event, has_processed_event, mark_event_processed

logger = logging.getLogger(__name__)


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _envelope_event_name(envelope: dict[str, Any]) -> str:
    value = envelope.get("event_name") or envelope.get("event_type") or ""
    return str(value)


def _decode_message_key(raw_key: object) -> str:
    if raw_key is None:
        return ""
    if isinstance(raw_key, bytes):
        try:
            return raw_key.decode("utf-8")
        except UnicodeDecodeError:
            return raw_key.hex()
    return str(raw_key)


def dispatch_event(topic: str, envelope: dict[str, Any], **context: Any) -> bool:
    handler = EVENT_HANDLERS.get(topic)
    if handler is None:
        logger.warning("No Kafka handler registered for topic=%s", topic)
        return False
    handler(envelope, **context)
    return True


def consume_events(run_duration: float | None = None, poll_interval: float | None = None) -> None:
    kafka_settings = get_kafka_settings()
    log_payload = _parse_bool(os.getenv("KAFKA_LOG_PAYLOAD"))

    if not kafka_settings.consumer_topics:
        logger.warning(
            "No Kafka topics configured for service=%s. Set KAFKA_CONSUMER_TOPICS to start the consumer.",
            kafka_settings.service_name,
        )
        return

    consumer = build_consumer()
    running = True
    deadline = time.monotonic() + run_duration if run_duration else None
    interval = poll_interval if poll_interval is not None else kafka_settings.poll_interval_seconds

    def shutdown(signum, frame) -> None:
        del signum, frame
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    consumer.subscribe(list(kafka_settings.consumer_topics))
    logger.info(
        "Kafka consumer started service=%s group=%s bootstrap=%s topics=%s",
        kafka_settings.service_name,
        kafka_settings.consumer_group,
        kafka_settings.bootstrap_servers,
        ",".join(kafka_settings.consumer_topics),
    )

    try:
        while running:
            if deadline and time.monotonic() >= deadline:
                break

            message = consumer.poll(interval)
            if message is None:
                continue

            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka consumer error: %s", message.error())
                continue

            envelope = decode_message_value(message.value())
            headers = normalize_headers(message.headers())
            event_id = str(envelope.get("event_id") or "")
            event_name = _envelope_event_name(envelope)
            message_key = _decode_message_key(message.key())

            logger.info(
                "Kafka event received service=%s group=%s topic=%s partition=%s offset=%s key=%s event_id=%s event_name=%s",
                kafka_settings.service_name,
                kafka_settings.consumer_group,
                message.topic(),
                message.partition(),
                message.offset(),
                message_key,
                event_id,
                event_name,
            )
            if log_payload:
                logger.info(
                    "Kafka event payload service=%s group=%s topic=%s partition=%s offset=%s event_id=%s payload=%s",
                    kafka_settings.service_name,
                    kafka_settings.consumer_group,
                    message.topic(),
                    message.partition(),
                    message.offset(),
                    event_id,
                    envelope,
                )

            if kafka_settings.enable_consumer_idempotency and has_processed_event(
                event_id,
                consumer_group=kafka_settings.consumer_group,
            ):
                logger.debug(
                    "Kafka event skipped (already processed) service=%s group=%s topic=%s partition=%s offset=%s event_id=%s event_name=%s",
                    kafka_settings.service_name,
                    kafka_settings.consumer_group,
                    message.topic(),
                    message.partition(),
                    message.offset(),
                    event_id,
                    event_name,
                )
                consumer.commit(message=message, asynchronous=False)
                continue

            try:
                close_old_connections()
                handled = dispatch_event(
                    message.topic(),
                    envelope,
                    message_key=message_key,
                    partition=message.partition(),
                    offset=message.offset(),
                    headers=headers,
                )
                if handled:
                    logger.info(
                        "Kafka event processed service=%s group=%s topic=%s partition=%s offset=%s event_id=%s event_name=%s",
                        kafka_settings.service_name,
                        kafka_settings.consumer_group,
                        message.topic(),
                        message.partition(),
                        message.offset(),
                        event_id,
                        event_name,
                    )
                else:
                    logger.warning(
                        "Kafka event ignored (no handler) service=%s group=%s topic=%s partition=%s offset=%s event_id=%s event_name=%s",
                        kafka_settings.service_name,
                        kafka_settings.consumer_group,
                        message.topic(),
                        message.partition(),
                        message.offset(),
                        event_id,
                        event_name,
                    )
                if kafka_settings.enable_consumer_idempotency:
                    mark_event_processed(
                        event_id=event_id,
                        consumer_group=kafka_settings.consumer_group,
                        topic=message.topic(),
                        envelope=envelope,
                        status="processed",
                    )
                consumer.commit(message=message, asynchronous=False)
            except Exception as exc:
                logger.exception(
                    "Kafka event failed service=%s group=%s topic=%s partition=%s offset=%s event_id=%s event_name=%s",
                    kafka_settings.service_name,
                    kafka_settings.consumer_group,
                    message.topic(),
                    message.partition(),
                    message.offset(),
                    event_id,
                    event_name,
                )
                should_commit = dead_letter_event(
                    topic=message.topic(),
                    consumer_group=kafka_settings.consumer_group,
                    envelope=envelope,
                    headers=headers,
                    error_message=str(exc),
                )
                logger.warning(
                    "Kafka event dead-lettered service=%s group=%s topic=%s partition=%s offset=%s event_id=%s event_name=%s committed=%s",
                    kafka_settings.service_name,
                    kafka_settings.consumer_group,
                    message.topic(),
                    message.partition(),
                    message.offset(),
                    event_id,
                    event_name,
                    should_commit,
                )
                if should_commit:
                    consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()
