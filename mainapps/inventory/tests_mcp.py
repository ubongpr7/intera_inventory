import asyncio
import os
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from starlette.testclient import TestClient

from mainapps.inventory.models import InventoryItem
from mainapps.stock.models import StockBalance, StockLocation
from mcp_server.server import (
    InventoryMcpPrincipal,
    _adjust_inventory_item_stock_via_view_sync,
    _transfer_stock_via_view_sync,
    _build_principal_from_token,
    _build_transport_security_settings,
    _create_stock_reservation_via_view_sync,
    _extract_bearer_token,
    _inventory_item_payload,
    _inventory_workflow_retry_sync,
    _inventory_workflow_cancel_sync,
    _inventory_adjustment_confirm_sync,
    _inventory_workflow_progress,
    _inventory_item_creation_start_sync,
    _inventory_setup_creation_confirm_sync,
    _purchase_order_creation_start_sync,
    _purchase_order_line_item_add_start_sync,
    _get_stock_risk_sync,
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
    def test_multi_line_inventory_workflow_progress_is_normalized(self):
        workflow = SimpleNamespace(
            status="succeeded",
            step="succeeded",
            state={"lines": [{"inventory_item_id": "item-1"}, {"inventory_item_id": "item-2"}]},
        )

        progress = _inventory_workflow_progress(workflow)

        self.assertEqual(progress["total"], 2)
        self.assertEqual(progress["completed"], 2)
        self.assertEqual(progress["percent"], 100)

    @patch("mcp_server.server.close_old_connections")
    @patch("mcp_server.server.transaction.atomic", return_value=nullcontext())
    @patch("mcp_server.server.InventoryMcpWorkflow.objects")
    def test_inventory_adjustment_confirmation_expires_without_mutation(self, workflow_manager, _atomic, _close_connections):
        workflow = SimpleNamespace(
            id="workflow-adjustment-expired",
            workflow_type="inventory_adjustment",
            status="active",
            step="awaiting_confirmation",
            expires_at=timezone.now() - timedelta(minutes=1),
            state={"confirmation_token": "token-expired", "inventory_item_id": "item-1", "lines": []},
            save=lambda **_kwargs: None,
        )
        workflow_manager.select_for_update.return_value.filter.return_value.first.return_value = workflow
        principal = InventoryMcpPrincipal(
            token="jwt", claims={}, user_id="11", profile_id=7,
            company_code="ACME", permissions={"interact_with_agent"},
        )

        with self.assertRaisesRegex(ValueError, "Workflow expired"):
            _inventory_adjustment_confirm_sync(
                principal=principal,
                workflow_id="workflow-adjustment-expired",
                confirmation_token="token-expired",
            )

        self.assertEqual(workflow.status, "failed")
        self.assertEqual(workflow.step, "expired")
        self.assertIsNone(workflow.state["confirmation_token"])

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

    @patch("mcp_server.server.close_old_connections")
    @patch("mcp_server.server.InventoryMcpWorkflow.objects.create")
    def test_inventory_item_creation_starts_with_validated_confirmation(self, create_workflow, _close_connections):
        def build_workflow(**kwargs):
            return SimpleNamespace(
                id="workflow-item-1",
                workflow_type="inventory_item_creation",
                state=kwargs["state"],
                status=kwargs["status"],
                step=kwargs["step"],
                expires_at=kwargs["expires_at"],
                save=lambda **_kwargs: None,
            )

        create_workflow.side_effect = build_workflow
        principal = InventoryMcpPrincipal(
            token="jwt-token", claims={}, user_id="11", profile_id=7,
            company_code="ACME", permissions={"interact_with_agent"},
        )

        result = _inventory_item_creation_start_sync(
            principal=principal,
            payload={"name_snapshot": "Widget", "track_stock": True, "reorder_point": "5"},
        )

        self.assertEqual(result["workflow_type"], "inventory_item_creation")
        self.assertEqual(result["interaction"]["action_type"], "create_inventory_item")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["contract"], "intera.workflow.v1")
        self.assertEqual(result["profile_id"], "")
        self.assertEqual(result["correlation_id"], "mcp-workflow:workflow-item-1")
        self.assertEqual(result["state"]["draft"]["name_snapshot"], "Widget")
        self.assertEqual(result["state"]["draft"]["reorder_point"], "5")

    @patch("mcp_server.server.close_old_connections")
    @patch("mcp_server.server.transaction.atomic", return_value=nullcontext())
    @patch("mcp_server.server._inventory_category_action_sync")
    @patch("mcp_server.server.InventoryMcpWorkflow.objects")
    def test_inventory_category_creation_applies_only_after_confirmation(
        self, workflow_manager, category_action, _atomic, _close_connections
    ):
        workflow = SimpleNamespace(
            id="workflow-category-1",
            workflow_type="inventory_category_creation",
            status="active",
            step="awaiting_confirmation",
            expires_at=timezone.now() + timedelta(minutes=10),
            state={
                "draft": {"name": "Raw materials", "structural": False},
                "confirmation_token": "confirm-category",
            },
            save=lambda **_kwargs: None,
        )
        workflow_manager.select_for_update.return_value.filter.return_value.first.return_value = workflow
        category_action.return_value = {"profile_id": 7, "category": {"id": "category-1", "name": "Raw materials"}}
        principal = InventoryMcpPrincipal(
            token="jwt-token", claims={}, user_id="11", profile_id=7,
            company_code="ACME", permissions={"interact_with_agent"},
        )

        result = _inventory_setup_creation_confirm_sync(
            principal=principal,
            workflow_id="workflow-category-1",
            confirmation_token="confirm-category",
            workflow_type="inventory_category_creation",
            action_type="create_inventory_category",
            action_sync=category_action,
            result_key="category",
            success_title="Inventory category created",
        )

        category_action.assert_called_once_with(
            principal=principal,
            action="create",
            method="post",
            data={"name": "Raw materials", "structural": False},
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["interaction"]["category"]["category"]["id"], "category-1")

    @patch("mcp_server.server.close_old_connections")
    @patch("mcp_server.server.InventoryMcpWorkflow.objects")
    def test_failed_adjustment_retry_reopens_confirmation(self, workflow_manager, _close_connections):
        workflow = SimpleNamespace(
            id="workflow-adjustment-1",
            workflow_type="inventory_adjustment",
            status="failed",
            step="failed",
            expires_at=timezone.now(),
            state={
                "lines": [{"inventory_item_id": "item-1", "quantity_delta": 2}],
                "error": {"code": "stock_adjustment_failed", "retryable": True},
            },
            save=lambda **_kwargs: None,
        )
        workflow_manager.filter.return_value.first.return_value = workflow
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="11",
            profile_id=7,
            company_code="ACME",
            permissions={"interact_with_agent"},
        )

        result = _inventory_workflow_retry_sync(principal=principal, workflow_id="workflow-adjustment-1")

        self.assertEqual(workflow.status, "active")
        self.assertEqual(workflow.step, "awaiting_confirmation")
        self.assertEqual(result["interaction"]["action_type"], "adjust_stock")
        self.assertTrue(result["interaction"]["confirmation_token"])

    @patch("mcp_server.server.close_old_connections")
    @patch("mcp_server.server.transaction.atomic", return_value=nullcontext())
    @patch("mcp_server.server.InventoryMcpWorkflow.objects")
    def test_inventory_workflow_cancel_is_non_mutating(self, workflow_manager, _atomic, _close_connections):
        workflow = SimpleNamespace(
            id="workflow-transfer-1",
            workflow_type="inventory_transfer",
            status="active",
            step="awaiting_confirmation",
            state={"confirmation_token": "token-1", "lines": [{"inventory_item_id": "item-1"}]},
            save=lambda **_kwargs: None,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        workflow_manager.select_for_update.return_value.filter.return_value.first.return_value = workflow
        principal = InventoryMcpPrincipal(token="jwt", claims={}, user_id="11", profile_id=7, company_code="ACME", permissions={"interact_with_agent"})

        result = _inventory_workflow_cancel_sync(principal=principal, workflow_id="workflow-transfer-1", reason="User cancelled")

        self.assertEqual(workflow.status, "cancelled")
        self.assertEqual(workflow.step, "cancelled")
        self.assertFalse(result["interaction"]["mutation_applied"])
        self.assertIsNone(workflow.state["confirmation_token"])

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


class InventoryMcpStockRiskTests(TransactionTestCase):
    def test_stock_risk_excludes_unallocated_items_but_keeps_zero_balances(self):
        profile_id = 8675309
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="1",
            profile_id=profile_id,
            company_code=None,
            permissions=set(),
        )
        location = StockLocation.objects.create(profile_id=profile_id, name="QA Shelf")
        unallocated_item = InventoryItem.objects.create(
            profile_id=profile_id,
            name_snapshot="Unallocated catalog item",
        )
        zero_balance_item = InventoryItem.objects.create(
            profile_id=profile_id,
            name_snapshot="Tracked zero-balance item",
        )
        StockBalance.objects.create(
            profile_id=profile_id,
            inventory_item=zero_balance_item,
            stock_location=location,
            quantity_on_hand=Decimal("0"),
        )

        payload = _get_stock_risk_sync(
            principal=principal,
            limit=25,
            expiring_days=30,
        )

        self.assertEqual(payload["summary"]["out_of_stock_count"], 1)
        self.assertEqual(
            [item["id"] for item in payload["risk_items"]["out_of_stock"]],
            [str(zero_balance_item.id)],
        )
        self.assertEqual(
            payload["risk_items"]["out_of_stock"][0]["location_name"],
            "QA Shelf",
        )
        self.assertNotIn(str(unallocated_item.id), [item["id"] for item in payload["risk_items"]["out_of_stock"]])


class InventoryMcpToolTests(SimpleTestCase):
    @patch("mcp_server.server.transaction.atomic")
    @patch("mcp_server.server._invoke_view_action_sync")
    def test_adjustment_bridge_applies_multiple_lines(self, invoke_view_action, atomic):
        invoke_view_action.side_effect = [{"new_quantity": 4}, {"new_quantity": 9}]
        principal = InventoryMcpPrincipal(
            token="jwt-token", claims={}, user_id="1", profile_id=1,
            company_code=None, permissions={"interact_with_agent"},
        )

        result = _adjust_inventory_item_stock_via_view_sync(
            principal=principal,
            inventory_item_id="item-1",
            data={
                "adjustments": [
                    {"inventory_item_id": "item-1", "stock_location_id": "loc-1", "quantity": "3", "adjustment_type": "add"},
                    {"inventory_item_id": "item-2", "stock_location_id": "loc-2", "quantity": "2", "adjustment_type": "remove"},
                ],
                "reason": "Cycle count",
            },
        )

        self.assertEqual(len(result["inventory_adjustments"]), 2)
        self.assertEqual(invoke_view_action.call_count, 2)
        self.assertEqual(invoke_view_action.call_args_list[1].kwargs["data"]["quantity_change"], "-2")

    @patch("mcp_server.server.transaction.atomic")
    @patch("mcp_server.server._invoke_view_action_sync")
    def test_transfer_bridge_applies_multiple_lines(self, invoke_view_action, atomic):
        invoke_view_action.side_effect = [{"ok": 1}, {"ok": 2}]
        principal = InventoryMcpPrincipal(
            token="jwt-token", claims={}, user_id="1", profile_id=1,
            company_code=None, permissions={"interact_with_agent"},
        )

        result = _transfer_stock_via_view_sync(
            principal=principal,
            location_id="source-1",
            data={
                "transfers": [
                    {"from_location_id": "source-1", "to_location_id": "dest-1", "inventory_item_id": "item-1", "quantity": "3"},
                    {"from_location_id": "source-1", "to_location_id": "dest-2", "inventory_item_id": "item-2", "quantity": "2"},
                ],
                "reason": "Rebalance",
            },
        )

        self.assertEqual(len(result["stock_transfers"]), 2)
        self.assertEqual(invoke_view_action.call_count, 2)
        self.assertEqual(invoke_view_action.call_args_list[1].kwargs["pk"], "source-1")

    @patch("mcp_server.server.InventoryMcpWorkflow.objects.create")
    def test_purchase_order_creation_starts_as_pending_draft(self, create_workflow):
        def build_workflow(**kwargs):
            return SimpleNamespace(
                id="workflow-1",
                workflow_type="purchase_order_creation",
                state=kwargs["state"],
                status=kwargs["status"],
                step=kwargs["step"],
                expires_at=kwargs["expires_at"],
                save=lambda **_kwargs: None,
            )

        create_workflow.side_effect = build_workflow
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="11",
            profile_id=7,
            company_code="ACME",
            permissions={"interact_with_agent"},
        )

        result = _purchase_order_creation_start_sync(
            principal=principal,
            payload={"description": "Restock warehouse", "supplier_reference": "SUP-9"},
        )

        draft = result["state"]["draft"]
        self.assertEqual(draft["status"], "pending")
        self.assertEqual(draft["workflow_state"], "DRAFT")
        self.assertEqual(result["interaction"]["interaction_type"], "confirmation_request")
        self.assertEqual(result["interaction"]["action_type"], "create_purchase_order")

    @patch("mcp_server.server._purchase_order_admin_action_sync")
    @patch("mcp_server.server.InventoryMcpWorkflow.objects.create")
    def test_purchase_order_line_item_starts_with_confirmation(
        self, create_workflow, purchase_order_action
    ):
        purchase_order_action.return_value = {
            "purchase_order": {"id": "po-1", "reference": "PO-001", "status": "pending"}
        }

        def build_workflow(**kwargs):
            return SimpleNamespace(
                id="workflow-line-1",
                workflow_type="purchase_order_line_item_add",
                state=kwargs["state"],
                status=kwargs["status"],
                step=kwargs["step"],
                expires_at=kwargs["expires_at"],
                save=lambda **_kwargs: None,
            )

        create_workflow.side_effect = build_workflow
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="11",
            profile_id=7,
            company_code="ACME",
            permissions={"interact_with_agent"},
        )

        result = _purchase_order_line_item_add_start_sync(
            principal=principal,
            purchase_order_id="po-1",
            payload={
                "inventory_item_id": "11111111-1111-4111-8111-111111111111",
                "quantity": 4,
                "unit_price": "12.50",
                "tax_rate": "7.5",
            },
        )

        self.assertEqual(result["interaction"]["interaction_type"], "confirmation_request")
        self.assertEqual(result["interaction"]["action_type"], "add_purchase_order_line_item")
        self.assertEqual(result["state"]["line_item"]["quantity"], "4")
        self.assertEqual(result["state"]["line_item"]["unit_price"], "12.50")

    def test_search_inventory_items_requires_authenticated_context(self):
        token = _principal_var.set(None)
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(search_inventory_items(query="warehouse"))
        finally:
            _principal_var.reset(token)

    def test_mutating_view_bridge_requires_agent_access(self):
        principal = InventoryMcpPrincipal(
            token="jwt-token",
            claims={},
            user_id="1",
            profile_id=1,
            company_code=None,
            permissions=set(),
        )
        with self.assertRaises(PermissionError):
            _invoke_view_action_sync(
                principal=principal,
                viewset_cls=object,
                action="create",
                method="post",
                data={},
            )

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
