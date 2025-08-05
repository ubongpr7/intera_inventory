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
    CompanyListAPIView, CompanyCreateAPIView, CompanyRetrieveAPIView,
    CompanyUpdateAPIView, CompanyDestroyAPIView   
)

# Company Tools
drf_publish_list_mcp_tool(
    CompanyListAPIView,
    instructions="Retrieve a list of all companies. Use this to get an overview of all organizational entities."
)

drf_publish_create_mcp_tool(
    CompanyCreateAPIView,
    instructions="Create a new company record. Provide 'name' (string, required, unique), 'address' (string, optional), 'phone_number' (string, optional), 'email' (string, optional), and 'website' (string, optional). Example: {'name': 'Acme Corp', 'address': '123 Business Rd'}"
)
