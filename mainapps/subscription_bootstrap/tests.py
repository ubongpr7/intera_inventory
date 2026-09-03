from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from .models import InventoryBootstrap
from .services import apply_subscription_activation


def snapshot(*, application="intera-ims", status="ACTIVE", paid=True, payment_status="COMPLETED", access_until=None):
    return {
        "application": application,
        "profile_id": "41",
        "subscription": {
            "id": str(uuid4()),
            "status": status,
            "billing_authorized": paid,
            "current_payment_status": payment_status,
            "access_until": access_until,
        },
    }


class SubscriptionBootstrapTests(TestCase):
    def test_no_subscription_does_not_create_bootstrap_or_tenant_records(self):
        payload = snapshot(status="CANCELLED", paid=False, payment_status="FAILED")
        self.assertIsNone(
            apply_subscription_activation(
                profile_id=41,
                subscription_id=payload["subscription"]["id"],
                activation_event_id="event-1",
                payload=payload,
            )
        )
        self.assertEqual(InventoryBootstrap.objects.count(), 0)

    def test_inventory_paid_subscription_creates_one_receipt(self):
        payload = snapshot(access_until=(timezone.now() + timedelta(days=30)).isoformat())
        args = {
            "profile_id": 41,
            "subscription_id": payload["subscription"]["id"],
            "payload": payload,
        }
        first = apply_subscription_activation(activation_event_id="event-1", **args)
        second = apply_subscription_activation(activation_event_id="event-2", **args)
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(InventoryBootstrap.objects.count(), 1)
        self.assertEqual(first.bootstrap_version, "inventory-bootstrap-v1")

    def test_hosperator_subscription_does_not_create_inventory_bootstrap(self):
        payload = snapshot(application="hosperator")
        self.assertIsNone(
            apply_subscription_activation(
                profile_id=41,
                subscription_id=payload["subscription"]["id"],
                activation_event_id="event-hosperator",
                payload=payload,
            )
        )
        self.assertEqual(InventoryBootstrap.objects.count(), 0)

    def test_trial_expired_and_cancelled_subscriptions_are_rejected(self):
        for index, payload in enumerate([
            snapshot(status="TRIAL"),
            snapshot(access_until=(timezone.now() - timedelta(minutes=1)).isoformat()),
            snapshot(status="CANCELLED", paid=False, payment_status="FAILED"),
        ]):
            self.assertIsNone(
                apply_subscription_activation(
                    profile_id=41 + index,
                    subscription_id=payload["subscription"]["id"],
                    activation_event_id=f"event-{index}",
                    payload=payload,
                )
            )
        self.assertEqual(InventoryBootstrap.objects.count(), 0)
