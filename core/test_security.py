from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.security import (
    is_production_environment,
    validate_notification_delivery_settings,
    validate_production_settings,
)


class ProductionSecurityValidationTests(SimpleTestCase):
    def test_recognizes_production_environment_names(self):
        self.assertTrue(is_production_environment("production"))
        self.assertTrue(is_production_environment("prod"))
        self.assertFalse(is_production_environment("development"))

    def test_accepts_explicit_secure_production_configuration(self):
        validate_production_settings(
            debug=False,
            local_server=False,
            allowed_hosts=["inventory.example.com"],
            cors_allow_all=False,
            cors_allowed_origins=["https://app.example.com"],
            csrf_trusted_origins=["https://app.example.com"],
            secure_ssl_redirect=True,
            session_cookie_secure=True,
            csrf_cookie_secure=True,
            auth_cookie_secure=True,
            hsts_seconds=31536000,
        )

    def test_rejects_development_safe_production_configuration(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "DEBUG must be disabled"):
            validate_production_settings(
                debug=True,
                local_server=True,
                allowed_hosts=["*"],
                cors_allow_all=True,
                cors_allowed_origins=["http://localhost:3000"],
                csrf_trusted_origins=["http://localhost:3000"],
                secure_ssl_redirect=False,
                session_cookie_secure=False,
                csrf_cookie_secure=False,
                auth_cookie_secure=False,
                hsts_seconds=0,
            )

    def test_notification_delivery_requires_https_and_non_default_signing_salt(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "NOTIFICATION_DOCUMENT_BASE_URL"):
            validate_notification_delivery_settings(
                purchase_order_mode="notification_service",
                return_order_mode="shadow",
                document_base_url="http://inventory.example.com",
                signing_salt="inventory-notification-document-v1",
            )

    def test_notification_delivery_accepts_staged_production_configuration(self):
        validate_notification_delivery_settings(
            purchase_order_mode="shadow",
            return_order_mode="direct",
            document_base_url="https://inventory.example.com",
            signing_salt="per-environment-signing-secret",
        )
