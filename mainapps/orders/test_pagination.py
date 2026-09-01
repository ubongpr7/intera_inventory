from unittest.mock import MagicMock, patch

from django.core.paginator import Paginator
from django.test import RequestFactory, SimpleTestCase
from rest_framework.request import Request

from mainapps.orders.pagination import PurchaseOrderPagination
from mainapps.orders.views import GoodsReceiptViewSet, PurchaseOrderViewSet, SalesOrderViewSet


class PurchaseOrderPaginationTests(SimpleTestCase):
    def test_purchase_order_endpoint_enables_pagination_and_filtering(self):
        self.assertIs(PurchaseOrderViewSet.pagination_class, PurchaseOrderPagination)
        self.assertEqual(
            [backend.__name__ for backend in PurchaseOrderViewSet.filter_backends],
            ["DjangoFilterBackend", "SearchFilter", "OrderingFilter"],
        )

    def test_response_includes_navigation_metadata(self):
        request = Request(RequestFactory().get("/order_api/purchase-orders/?page=2&page_size=20"))
        pagination = PurchaseOrderPagination()
        pagination.request = request
        pagination.page = Paginator(range(104), 20).page(2)

        response = pagination.get_paginated_response(["order"])

        self.assertEqual(response.data["count"], 104)
        self.assertEqual(response.data["page"], 2)
        self.assertEqual(response.data["page_size"], 20)
        self.assertEqual(response.data["total_pages"], 6)
        self.assertEqual(response.data["results"], ["order"])


class GoodsReceiptPaginationCacheTests(SimpleTestCase):
    def test_goods_receipt_list_cache_is_disabled_for_workflow_freshness(self):
        self.assertFalse(GoodsReceiptViewSet.CACHE_ENABLED)


class SalesOrderSummaryContractTests(SimpleTestCase):
    def test_summary_response_includes_operational_counts(self):
        queryset = MagicMock()
        queryset.count.return_value = 12
        queryset.exclude.return_value.count.return_value = 9
        queryset.filter.side_effect = [
            MagicMock(count=MagicMock(return_value=3)),
            MagicMock(count=MagicMock(return_value=4)),
            MagicMock(count=MagicMock(return_value=2)),
            MagicMock(count=MagicMock(return_value=2)),
        ]
        request = RequestFactory().get("/order_api/sales-orders/summary/")
        view = SalesOrderViewSet()

        with patch.object(SalesOrderViewSet, "get_queryset", return_value=queryset):
            with patch.object(SalesOrderViewSet, "filter_queryset", return_value=queryset):
                response = SalesOrderViewSet.summary(view, request)

        self.assertEqual(response.data["total_orders"], 12)
        self.assertEqual(response.data["active_orders"], 9)
        self.assertEqual(response.data["pending_orders"], 3)
        self.assertEqual(response.data["in_progress_orders"], 4)
        self.assertEqual(response.data["shipped_orders"], 2)
        self.assertEqual(response.data["ready_to_close_orders"], 2)
