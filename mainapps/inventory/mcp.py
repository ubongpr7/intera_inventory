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

from inventory.mcp_views import (
    InventoryListAPIView, InventoryCreateAPIView, InventoryRetrieveAPIView,
    InventoryUpdateAPIView, InventoryDestroyAPIView,
)

# Inventory Tools
drf_publish_list_mcp_tool(
    InventoryListAPIView,
    instructions="Retrieve a list of all inventory records. Use this to get an overview of all inventories in the system."
)

drf_publish_create_mcp_tool(
    InventoryCreateAPIView,
    instructions="Create a new inventory record. Provide 'name' (string, required) and 'location' (string, optional). Example: {'name': 'Warehouse A', 'location': 'New York'}"
)


drf_publish_update_mcp_tool(
    InventoryUpdateAPIView,
    instructions="Update an existing inventory record by its ID. Provide the 'id' (integer, required) as a path parameter and fields to update in the body. Example: {'id': 1, 'name': 'Main Warehouse'}"
)

drf_publish_destroy_mcp_tool(
    InventoryDestroyAPIView,
    instructions="Delete an inventory record by its ID. Provide the 'id' (integer, required) as a path parameter. Use with caution."
)

