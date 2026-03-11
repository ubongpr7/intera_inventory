from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_CONSUMER_TOPICS = (
    "identity.user",
    "identity.company_profile",
    "identity.membership",
    "catalog.product",
    "catalog.variant",
    "pos.order",
)


def _parse_csv(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class KafkaSettings:
    service_name: str
    bootstrap_servers: str
    security_protocol: str
    sasl_mechanism: str | None
    sasl_username: str | None
    sasl_password: str | None
    ssl_ca_location: str | None
    ssl_certificate_location: str | None
    ssl_key_location: str | None
    ssl_key_password: str | None
    consumer_group: str
    consumer_topics: tuple[str, ...]
    auto_offset_reset: str
    request_timeout_ms: int
    message_timeout_ms: int
    session_timeout_ms: int
    heartbeat_interval_ms: int
    producer_linger_ms: int
    producer_acks: str
    poll_interval_seconds: float
    use_outbox: bool
    enable_consumer_idempotency: bool
    enable_dlq: bool
    commit_failed_messages: bool
    dlq_suffix: str
    outbox_batch_size: int
    outbox_poll_interval_seconds: float
    outbox_retry_delay_seconds: int

    @classmethod
    def from_env(cls) -> "KafkaSettings":
        service_name = os.getenv("KAFKA_SERVICE_NAME", "inventory")
        return cls(
            service_name=service_name,
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            security_protocol=os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").upper(),
            sasl_mechanism=os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
            sasl_username=os.getenv("KAFKA_SASL_USERNAME") or None,
            sasl_password=os.getenv("KAFKA_SASL_PASSWORD") or None,
            ssl_ca_location=os.getenv("KAFKA_SSL_CA_FILE") or None,
            ssl_certificate_location=os.getenv("KAFKA_SSL_CERT_FILE") or None,
            ssl_key_location=os.getenv("KAFKA_SSL_KEY_FILE") or None,
            ssl_key_password=os.getenv("KAFKA_SSL_KEY_PASSWORD") or None,
            consumer_group=os.getenv("KAFKA_CONSUMER_GROUP", f"{service_name}-consumer"),
            consumer_topics=_parse_csv(os.getenv("KAFKA_CONSUMER_TOPICS"), DEFAULT_CONSUMER_TOPICS),
            auto_offset_reset=os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest"),
            request_timeout_ms=_parse_int(os.getenv("KAFKA_REQUEST_TIMEOUT_MS"), 10000),
            message_timeout_ms=_parse_int(os.getenv("KAFKA_MESSAGE_TIMEOUT_MS"), 10000),
            session_timeout_ms=_parse_int(os.getenv("KAFKA_SESSION_TIMEOUT_MS"), 10000),
            heartbeat_interval_ms=_parse_int(os.getenv("KAFKA_HEARTBEAT_INTERVAL_MS"), 3000),
            producer_linger_ms=_parse_int(os.getenv("KAFKA_PRODUCER_LINGER_MS"), 5),
            producer_acks=os.getenv("KAFKA_PRODUCER_ACKS", "all"),
            poll_interval_seconds=_parse_float(os.getenv("KAFKA_POLL_INTERVAL"), 1.0),
            use_outbox=_parse_bool(os.getenv("KAFKA_USE_OUTBOX"), False),
            enable_consumer_idempotency=_parse_bool(os.getenv("KAFKA_ENABLE_CONSUMER_IDEMPOTENCY"), True),
            enable_dlq=_parse_bool(os.getenv("KAFKA_ENABLE_DLQ"), True),
            commit_failed_messages=_parse_bool(os.getenv("KAFKA_COMMIT_FAILED_MESSAGES"), True),
            dlq_suffix=os.getenv("KAFKA_DLQ_SUFFIX", ".dlq"),
            outbox_batch_size=_parse_int(os.getenv("KAFKA_OUTBOX_BATCH_SIZE"), 100),
            outbox_poll_interval_seconds=_parse_float(os.getenv("KAFKA_OUTBOX_POLL_INTERVAL"), 2.0),
            outbox_retry_delay_seconds=_parse_int(os.getenv("KAFKA_OUTBOX_RETRY_DELAY_SECONDS"), 30),
        )

    def _apply_security(self, config: dict[str, object]) -> dict[str, object]:
        config["security.protocol"] = self.security_protocol

        if self.security_protocol.startswith("SASL"):
            if not self.sasl_username or not self.sasl_password:
                raise RuntimeError(
                    "KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD must be set when using SASL Kafka security."
                )
            config["sasl.mechanism"] = self.sasl_mechanism or "PLAIN"
            config["sasl.username"] = self.sasl_username
            config["sasl.password"] = self.sasl_password

        if self.security_protocol in {"SSL", "SASL_SSL"}:
            if self.ssl_ca_location:
                config["ssl.ca.location"] = self.ssl_ca_location
            if self.ssl_certificate_location:
                config["ssl.certificate.location"] = self.ssl_certificate_location
            if self.ssl_key_location:
                config["ssl.key.location"] = self.ssl_key_location
            if self.ssl_key_password:
                config["ssl.key.password"] = self.ssl_key_password

        return config

    def producer_config(self) -> dict[str, object]:
        config: dict[str, object] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": f"{self.service_name}-producer",
            "enable.idempotence": True,
            "acks": self.producer_acks,
            "socket.timeout.ms": self.request_timeout_ms,
            "message.timeout.ms": self.message_timeout_ms,
            "linger.ms": self.producer_linger_ms,
        }
        return self._apply_security(config)

    def consumer_config(self) -> dict[str, object]:
        config: dict[str, object] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": f"{self.service_name}-consumer",
            "group.id": self.consumer_group,
            "auto.offset.reset": self.auto_offset_reset,
            "enable.auto.commit": False,
            "session.timeout.ms": self.session_timeout_ms,
            "heartbeat.interval.ms": self.heartbeat_interval_ms,
        }
        return self._apply_security(config)


@lru_cache(maxsize=1)
def get_kafka_settings() -> KafkaSettings:
    return KafkaSettings.from_env()
