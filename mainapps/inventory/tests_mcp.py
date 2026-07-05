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
    _adjust_inventory_item_stock_via_view_sync,
    _build_principal_from_token,
    _build_transport_security_settings,
    _create_stock_reservation_via_view_sync,
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

    @patch("mcp_server.server._search_inventory_items_sync")
    def test_search_inventory_items_forwards_structural_location_scope(self, search_sync):
        search_sync.return_value = {"count": 0, "results": []}
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="1",
            profile_id=1,
            company_code=None,
            permissions=set(),
        )
        token = _principal_var.set(principal)
        try:
            result = asyncio.run(
                search_inventory_items(
                    query="perfume",
                    structural_location_id="6e5bb31f-0d7c-4f55-a4c6-b9730d0f6f35",
                )
            )
        finally:
            _principal_var.reset(token)

        self.assertEqual(result, {"count": 0, "results": []})
        _, kwargs = search_sync.call_args
        self.assertEqual(kwargs["structural_location_id"], "6e5bb31f-0d7c-4f55-a4c6-b9730d0f6f35")

    @patch("mcp_server.server._invoke_view_action_sync")
    def test_adjust_inventory_item_stock_forwards_structural_location_scope(self, invoke_view_action_sync):
        invoke_view_action_sync.return_value = {"ok": True}
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="1",
            profile_id=1,
            company_code=None,
            permissions=set(),
        )

        payload = _adjust_inventory_item_stock_via_view_sync(
            principal=principal,
            inventory_item_id="item-1",
            data={
                "adjustments": [
                    {
                        "stock_location_id": "location-1",
                        "structural_location_id": "structural-1",
                        "quantity": "5",
                        "adjustment_type": "add",
                    }
                ]
            },
        )

        self.assertEqual(payload["inventory_adjustment"], {"ok": True})
        _, kwargs = invoke_view_action_sync.call_args
        self.assertEqual(kwargs["data"]["location_id"], "location-1")
        self.assertEqual(kwargs["data"]["structural_location_id"], "structural-1")

    @patch("mcp_server.server._invoke_view_action_sync")
    def test_create_stock_reservation_preserves_structural_location_scope(self, invoke_view_action_sync):
        invoke_view_action_sync.return_value = {
            "id": "reservation-1",
            "inventory_item_id": "item-1",
            "stock_location_id": "location-1",
            "reserved_quantity": 2,
            "fulfilled_quantity": 0,
            "remaining_quantity": 2,
            "status": "active",
        }
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="1",
            profile_id=1,
            company_code=None,
            permissions=set(),
        )

        _create_stock_reservation_via_view_sync(
            principal=principal,
            data={
                "inventory_item_id": "item-1",
                "stock_location_id": "location-1",
                "reserved_quantity": "2",
                "structural_location_id": "structural-1",
            },
        )

        _, kwargs = invoke_view_action_sync.call_args
        self.assertEqual(kwargs["data"]["stock_location_id"], "location-1")
        self.assertEqual(kwargs["data"]["location_id"], "location-1")
        self.assertEqual(kwargs["data"]["structural_location_id"], "structural-1")

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
