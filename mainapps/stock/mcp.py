
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


from stock.mcp_views import (
    StockLocationListAPIView, StockLocationCreateAPIView, StockLocationRetrieveAPIView,
    StockLocationUpdateAPIView, StockLocationDestroyAPIView,
    StockItemListAPIView, StockItemCreateAPIView, StockItemRetrieveAPIView,
    StockItemUpdateAPIView, StockItemDestroyAPIView,
    StockAdjustmentListAPIView, StockAdjustmentCreateAPIView, StockAdjustmentRetrieveAPIView,
    StockAdjustmentUpdateAPIView, StockAdjustmentDestroyAPIView
)

# Stock Location Tools
drf_publish_list_mcp_tool(
    StockLocationListAPIView,
    instructions="Retrieve a list of all stock locations. Use this to see all available storage places for inventory."
)

drf_publish_create_mcp_tool(
    StockLocationCreateAPIView,
    instructions="Create a new stock location. Provide 'name' (string, required, unique), 'address' (string, optional), and 'is_active' (boolean, optional). Example: {'name': 'Warehouse B', 'address': '123 Main St', 'is_active': True}"
)

# global_mcp_server.register_drf_retrieve_tool(
#     StockLocationRetrieveAPIView,
#     name="RetrieveStockLocation",
#     instructions="Get detailed information about a specific stock location by its ID. Provide the 'id' (integer, required) as a path parameter."
# )

drf_publish_update_mcp_tool(
    StockLocationUpdateAPIView,
    instructions="Update an existing stock location by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'is_active': False}"
)

drf_publish_destroy_mcp_tool(
    StockLocationDestroyAPIView,
    instructions="Delete a stock location by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)

# Stock Item Tools
drf_publish_list_mcp_tool(
    StockItemListAPIView,
    instructions="Retrieve a list of all stock items. Use this to see all products currently in stock at various locations."
)

drf_publish_create_mcp_tool(
    StockItemCreateAPIView,
    instructions="Create a new stock item. Provide 'product_id' (string, required), 'location' (integer, required, ID of StockLocation), and 'quantity' (integer, required). Example: {'product_id': 'PROD002', 'location': 2, 'quantity': 50}"
)

# global_mcp_server.register_drf_retrieve_tool(
#     StockItemRetrieveAPIView,
#     name="RetrieveStockItem",
#     instructions="Get detailed information about a specific stock item by its ID. Provide the 'id' (integer, required) as a path parameter."
# )

drf_publish_update_mcp_tool(
    StockItemUpdateAPIView,
    instructions="Update an existing stock item by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'quantity': 75}"
)

drf_publish_destroy_mcp_tool(
    StockItemDestroyAPIView,
    instructions="Delete a stock item by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)

# Stock Adjustment Tools
drf_publish_list_mcp_tool(
    StockAdjustmentListAPIView,
    instructions="Retrieve a list of all stock adjustments. Useful for auditing stock movements and changes."
)

drf_publish_create_mcp_tool(
    StockAdjustmentCreateAPIView,
    instructions="Create a new stock adjustment. Provide 'stock_item' (integer, required, ID of StockItem), 'adjustment_type' (string, required, 'add', 'remove', or 'transfer'), 'quantity_change' (integer, required), 'reason' (string, optional), and 'adjusted_by' (string, optional). Example: {'stock_item': 1, 'adjustment_type': 'add', 'quantity_change': 10, 'reason': 'Received new shipment'}"
)

# global_mcp_server.register_drf_retrieve_tool(
#     StockAdjustmentRetrieveAPIView,
#     name="RetrieveStockAdjustment",
#     instructions="Get detailed information about a specific stock adjustment by its ID. Provide the 'id' (integer, required) as a path parameter."
# )

drf_publish_update_mcp_tool(
    StockAdjustmentUpdateAPIView,
    instructions="Update an existing stock adjustment by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'quantity_change': 15}"
)

drf_publish_destroy_mcp_tool(
    StockAdjustmentDestroyAPIView,
    instructions="Delete a stock adjustment by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)
