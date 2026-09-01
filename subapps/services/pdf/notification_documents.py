from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.urls import reverse


class NotificationDocumentError(ValueError):
    pass


def build_purchase_order_pdf_url(purchase_order) -> str:
    base_url = (settings.NOTIFICATION_DOCUMENT_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        raise NotificationDocumentError("NOTIFICATION_DOCUMENT_BASE_URL must be configured for notification delivery.")
    token = signing.dumps(
        {"purchase_order_id": str(purchase_order.id)},
        salt=settings.NOTIFICATION_DOCUMENT_SIGNING_SALT,
        compress=True,
    )
    path = reverse("purchase-order-notification-pdf", kwargs={"pk": purchase_order.id})
    return f"{base_url}{path}?{urlencode({'token': token})}"


def build_return_order_pdf_url(return_order) -> str:
    base_url = (settings.NOTIFICATION_DOCUMENT_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        raise NotificationDocumentError("NOTIFICATION_DOCUMENT_BASE_URL must be configured for notification delivery.")
    token = signing.dumps(
        {"return_order_id": str(return_order.id)},
        salt=settings.NOTIFICATION_DOCUMENT_SIGNING_SALT,
        compress=True,
    )
    path = reverse("return-order-notification-pdf", kwargs={"pk": return_order.id})
    return f"{base_url}{path}?{urlencode({'token': token})}"


def verify_purchase_order_pdf_token(*, purchase_order_id: str, token: str) -> None:
    try:
        payload = signing.loads(
            token,
            salt=settings.NOTIFICATION_DOCUMENT_SIGNING_SALT,
            max_age=settings.NOTIFICATION_DOCUMENT_URL_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise NotificationDocumentError("The document link is invalid or has expired.") from exc
    if str(payload.get("purchase_order_id") or "") != str(purchase_order_id):
        raise NotificationDocumentError("The document link does not match this purchase order.")


def verify_return_order_pdf_token(*, return_order_id: str, token: str) -> None:
    try:
        payload = signing.loads(
            token,
            salt=settings.NOTIFICATION_DOCUMENT_SIGNING_SALT,
            max_age=settings.NOTIFICATION_DOCUMENT_URL_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise NotificationDocumentError("The document link is invalid or has expired.") from exc
    if str(payload.get("return_order_id") or "") != str(return_order_id):
        raise NotificationDocumentError("The document link does not match this return order.")
