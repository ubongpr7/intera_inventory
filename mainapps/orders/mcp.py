from django.db.models import QuerySet, Q
from mcp_server import mcp_server as mcp
from mcp_server import (
    MCPToolset,
    drf_serialize_output,
    drf_publish_create_mcp_tool,
    drf_publish_update_mcp_tool,
    drf_publish_destroy_mcp_tool,
    drf_publish_list_mcp_tool,
)


from .mcp_views import (
    PurchaseOrderListAPIView, PurchaseOrderCreateAPIView, PurchaseOrderRetrieveAPIView,
    PurchaseOrderUpdateAPIView, PurchaseOrderDestroyAPIView,
    SalesOrderListAPIView, SalesOrderCreateAPIView, SalesOrderRetrieveAPIView,
    SalesOrderUpdateAPIView, SalesOrderDestroyAPIView,
    PurchaseOrderLineItemListAPIView, PurchaseOrderLineItemCreateAPIView,
    PurchaseOrderLineItemUpdateAPIView, PurchaseOrderLineItemDestroyAPIView
)

drf_publish_list_mcp_tool(
    PurchaseOrderListAPIView,
    instructions="Retrieve a list of all purchase orders. Use this to track incoming inventory from suppliers."
)

drf_publish_create_mcp_tool(
    PurchaseOrderCreateAPIView,
    instructions="Create a new purchase order. Provide 'supplier_name' (string, required), 'expected_delivery_date' (date, optional, YYYY-MM-DD), 'status' (string, optional, e.g., 'pending', 'approved'), and 'total_amount' (decimal, optional). Example: {'supplier_name': 'Supplier A', 'expected_delivery_date': '2025-09-01', 'total_amount': 500.00}"
)

# global_mcp_server.register_drf_retrieve_tool(
#     PurchaseOrderRetrieveAPIView,
#     name="RetrievePurchaseOrder",
#     instructions="Get detailed information about a specific purchase order by its ID. Provide the 'id' (integer, required) as a path parameter."
# )

drf_publish_update_mcp_tool(
    PurchaseOrderUpdateAPIView,
    instructions="Update an existing purchase order by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'status': 'approved'}"
)

drf_publish_destroy_mcp_tool(
    PurchaseOrderDestroyAPIView,
    instructions="Delete a purchase order by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)

# Sales Order Tools
drf_publish_list_mcp_tool(
    SalesOrderListAPIView,
    instructions="Retrieve a list of all sales orders. Use this to track outgoing inventory to customers."
)

drf_publish_create_mcp_tool(
    SalesOrderCreateAPIView,
    instructions="Create a new sales order. Provide 'customer_name' (string, required), 'delivery_date' (date, optional, YYYY-MM-DD), 'status' (string, optional, e.g., 'pending', 'shipped'), and 'total_amount' (decimal, optional). Example: {'customer_name': 'Customer X', 'delivery_date': '2025-08-15', 'total_amount': 150.00}"
)

# global_mcp_server.register_drf_retrieve_tool(
#     SalesOrderRetrieveAPIView,
#     name="RetrieveSalesOrder",
#     instructions="Get detailed information about a specific sales order by its ID. Provide the 'id' (integer, required) as a path parameter."
# )

drf_publish_update_mcp_tool(
    SalesOrderUpdateAPIView,
    instructions="Update an existing sales order by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'status': 'shipped'}"
)

drf_publish_destroy_mcp_tool(
    SalesOrderDestroyAPIView,
    instructions="Delete a sales order by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)

# Order Item Tools
drf_publish_list_mcp_tool(
    PurchaseOrderLineItemListAPIView,
    instructions="Retrieve a list of all individual items within purchase or sales orders. Useful for seeing what products are part of orders."
)

drf_publish_create_mcp_tool(
    PurchaseOrderLineItemCreateAPIView,
    instructions="Create a new order item. Provide 'product_id' (string, required), 'quantity' (integer, required), 'unit_price' (decimal, required), and either 'purchase_order' (integer, ID) or 'sales_order' (integer, ID). Example: {'purchase_order': 1, 'product_id': 'PROD003', 'quantity': 5, 'unit_price': 10.00}"
)

# global_mcp_server.register_drf_retrieve_tool(
#     PurchaseOrderLineItemRetrieveAPIView,
#     name="RetrievePurchaseOrderLineItem",
#     instructions="Get detailed information about a specific order item by its ID. Provide the 'id' (integer, required) as a path parameter."
# )

drf_publish_update_mcp_tool(
    PurchaseOrderLineItemUpdateAPIView,
    instructions="Update an existing order item by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'quantity': 7}"
)

drf_publish_destroy_mcp_tool(
    PurchaseOrderLineItemDestroyAPIView,
    instructions="Delete an order item by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)
