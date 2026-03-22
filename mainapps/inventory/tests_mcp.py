import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from starlette.testclient import TestClient

from mainapps.inventory.models import InventoryItem
from mcp_server.server import (
    InventoryMcpPrincipal,
    _build_principal_from_token,
    _build_transport_security_settings,
    _extract_bearer_token,
    _inventory_item_payload,
    _invoke_view_action_sync,
    _principal_var,
    app as inventory_mcp_app,
    search_inventory_items,
)


class InventoryMcpAuthTests(SimpleTestCase):
    def test_extract_bearer_token_requires_bearer_scheme(self):
        self.assertEqual(_extract_bearer_token("Bearer token-123"), "token-123")
        self.assertIsNone(_extract_bearer_token("Basic token-123"))
        self.assertIsNone(_extract_bearer_token("Bearer "))

    @patch("mcp_server.server.UntypedToken")
    def test_build_principal_from_token_reads_claims(self, token_cls):
        token_cls.return_value.payload = {
            "user_id": 42,
            "profile_id": 9,
            "company_code": "ACME",
            "permissions": ["read_inventory_item"],
        }

        principal = _build_principal_from_token("jwt-token")

        self.assertEqual(principal.user_id, "42")
        self.assertEqual(principal.profile_id, 9)
        self.assertEqual(principal.company_code, "ACME")
        self.assertEqual(principal.permissions, {"read_inventory_item"})

    @patch.dict(
        os.environ,
        {
            "ALLOWED_HOSTS": "inventory.mcp.interaims.com,inventory.interaims.com",
            "CORS_ALLOWED_ORIGINS": "http://localhost:3000,https://dev.interaims.com",
        },
        clear=False,
    )
    def test_transport_security_uses_configured_hosts(self):
        settings = _build_transport_security_settings()

        self.assertIn("inventory.mcp.interaims.com", settings.allowed_hosts)
        self.assertIn("inventory.interaims.com", settings.allowed_hosts)
        self.assertIn("http://localhost:3000", settings.allowed_origins)


class InventoryMcpSerializationTests(SimpleTestCase):
    def test_inventory_item_payload_includes_summary_fields(self):
        inventory_item = InventoryItem(
            name_snapshot="Main Warehouse",
            profile="1",
            profile_id=1,
            inventory_type="raw_material",
            track_stock=True,
            track_lot=True,
            reorder_point=10,
            reorder_quantity=25,
            minimum_stock_level=5,
        )

        payload = _inventory_item_payload(
            inventory_item,
            summary={
                "quantity": Decimal("12"),
                "quantity_reserved": Decimal("2"),
                "quantity_available": Decimal("10"),
                "total_stock_value": Decimal("250"),
                "status": "ACTIVE",
                "location_breakdown": [{"location_name": "Rack A", "quantity": Decimal("12")}],
            },
        )

        self.assertEqual(payload["name"], "Main Warehouse")
        self.assertEqual(payload["status"], "ACTIVE")
        self.assertEqual(payload["quantity"], 12.0)
        self.assertEqual(payload["location_breakdown"][0]["location_name"], "Rack A")

    def test_inventory_item_payload_includes_tracking_summary(self):
        inventory_item = InventoryItem(
            name_snapshot="Printer Toner",
            sku_snapshot="TON-001",
            barcode_snapshot="B-123",
            inventory_type="finished_goods",
            track_stock=True,
            track_lot=True,
            track_serial=False,
            track_expiry=True,
        )

        payload = _inventory_item_payload(
            inventory_item,
            summary={
                "quantity": Decimal("18"),
                "quantity_reserved": Decimal("3"),
                "quantity_available": Decimal("15"),
                "status": "ACTIVE",
                "serial_count": 0,
                "lot_count": 2,
                "location_breakdown": [{"location_name": "Rack B", "quantity": Decimal("18")}],
            },
        )

        self.assertEqual(payload["name"], "Printer Toner")
        self.assertEqual(payload["quantity"], 18.0)
        self.assertEqual(payload["lot_count"], 2)
        self.assertEqual(payload["location_breakdown"][0]["location_name"], "Rack B")


class InventoryMcpToolTests(SimpleTestCase):
    def test_search_inventory_items_requires_authenticated_context(self):
        token = _principal_var.set(None)
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(search_inventory_items(query="warehouse"))
        finally:
            _principal_var.reset(token)

    @patch("mcp_server.server.APIRequestFactory.get")
    def test_invoke_view_action_sync_omits_none_query_params_from_get_request(self, factory_get):
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="1",
            profile_id=1,
            company_code=None,
            permissions=set(),
        )

        captured_request = object()
        factory_get.return_value = captured_request

        class _DummyViewSet:
            @staticmethod
            def as_view(actions):
                _ = actions
                return lambda request, pk=None: SimpleNamespace(status_code=200, data={"request_matches": request is captured_request, "pk": pk})

        payload = _invoke_view_action_sync(
            principal=principal,
            viewset_cls=_DummyViewSet,
            action="list",
            method="get",
            query_params={
                "search": "",
                "is_active": None,
                "page_size": 25,
            },
        )

        self.assertEqual(payload, {"request_matches": True, "pk": None})
        _, kwargs = factory_get.call_args
        self.assertEqual(kwargs["data"], {"page_size": 25})
        self.assertEqual(kwargs["HTTP_AUTHORIZATION"], "Bearer jwt-token")


class InventoryMcpAppTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mcp_client_ctx = TestClient(inventory_mcp_app, base_url="http://127.0.0.1:8000")
        cls.mcp_client = cls.mcp_client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.mcp_client_ctx.__exit__(None, None, None)
        super().tearDownClass()

    def test_health_endpoint_is_available(self):
        response = self.mcp_client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_mcp_mount_initializes_without_server_error(self):
        redirect = self.mcp_client.get("/mcp", follow_redirects=False)
        response = self.mcp_client.get("/mcp/", headers={"accept": "application/json"})

        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "http://127.0.0.1:8000/mcp/")
        self.assertEqual(response.status_code, 406)
