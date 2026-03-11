from __future__ import annotations

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


def dispatch_event(topic: str, envelope: dict[str, Any], **context: Any) -> bool:
    handler = EVENT_HANDLERS.get(topic)
    if handler is None:
        logger.warning("No Kafka handler registered for topic=%s", topic)
        return False
    handler(envelope, **context)
    return True


def consume_events(run_duration: float | None = None, poll_interval: float | None = None) -> None:
    kafka_settings = get_kafka_settings()
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
            raw_key = message.key()
            event_id = str(envelope.get("event_id") or "")

            if kafka_settings.enable_consumer_idempotency and has_processed_event(
                event_id,
                consumer_group=kafka_settings.consumer_group,
            ):
                consumer.commit(message=message, asynchronous=False)
                continue

            try:
                close_old_connections()
                dispatch_event(
                    message.topic(),
                    envelope,
                    message_key=raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key,
                    partition=message.partition(),
                    offset=message.offset(),
                    headers=headers,
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
                    "Failed handling Kafka event topic=%s partition=%s offset=%s",
                    message.topic(),
                    message.partition(),
                    message.offset(),
                )
                should_commit = dead_letter_event(
                    topic=message.topic(),
                    consumer_group=kafka_settings.consumer_group,
                    envelope=envelope,
                    headers=headers,
                    error_message=str(exc),
                )
                if should_commit:
                    consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()
