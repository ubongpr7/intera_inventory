from __future__ import annotations

from django.db import models
from django.utils import timezone


class KafkaOutboxStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    PUBLISHED = "published", "Published"
    FAILED = "failed", "Failed"


class KafkaConsumedEventStatus(models.TextChoices):
    PROCESSED = "processed", "Processed"
    DEAD_LETTERED = "dead_lettered", "Dead Lettered"


class KafkaDeadLetterStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    REPLAYED = "replayed", "Replayed"


class KafkaOutboxEvent(models.Model):
    event_id = models.CharField(primary_key=True, max_length=100)
    topic = models.CharField(max_length=255, db_index=True)
    event_name = models.CharField(max_length=255, db_index=True)
    event_key = models.CharField(max_length=255, blank=True, default="")
    source_service = models.CharField(max_length=120, db_index=True)
    event_version = models.PositiveIntegerField(default=1)
    message_json = models.JSONField(default=dict)
    headers_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=KafkaOutboxStatus.choices,
        default=KafkaOutboxStatus.PENDING,
        db_index=True,
    )
    publish_attempts = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"]),
            models.Index(fields=["topic", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.topic}:{self.event_name}:{self.event_id}"


class KafkaConsumedEvent(models.Model):
    event_id = models.CharField(max_length=100)
    consumer_group = models.CharField(max_length=255)
    topic = models.CharField(max_length=255, db_index=True)
    event_name = models.CharField(max_length=255, blank=True, default="")
    source_service = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=KafkaConsumedEventStatus.choices,
        default=KafkaConsumedEventStatus.PROCESSED,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-processed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event_id", "consumer_group"],
                name="inventory_kafka_consumed_event_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["consumer_group", "processed_at"]),
            models.Index(fields=["topic", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.consumer_group}:{self.event_id}:{self.status}"


class KafkaDeadLetterEvent(models.Model):
    event_id = models.CharField(max_length=100, db_index=True)
    topic = models.CharField(max_length=255, db_index=True)
    dead_letter_topic = models.CharField(max_length=255, blank=True, default="")
    consumer_group = models.CharField(max_length=255, db_index=True)
    event_name = models.CharField(max_length=255, blank=True, default="")
    source_service = models.CharField(max_length=120, blank=True, default="")
    message_json = models.JSONField(default=dict)
    headers_json = models.JSONField(default=dict, blank=True)
    error_message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=KafkaDeadLetterStatus.choices,
        default=KafkaDeadLetterStatus.PENDING,
        db_index=True,
    )
    failed_at = models.DateTimeField(default=timezone.now, db_index=True)
    replayed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-failed_at"]
        indexes = [
            models.Index(fields=["consumer_group", "status"]),
            models.Index(fields=["topic", "failed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.consumer_group}:{self.topic}:{self.event_id}:{self.status}"
