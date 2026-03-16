# Pharmacy Inventory Management System

![Pharm-Inventory Logo](static/images/logos/logo.png)

A comprehensive Django-based inventory management system for pharmaceutical operations.

## Features
- **Inventory Management**: Track drug stock levels, batches, and expiration dates
- **Order Processing**: Manage purchase and sales orders
- **User Management**: Role-based access control
- **Reporting**: Generate inventory and sales reports
- **API**: RESTful API for integration

## Quick Start
```bash
# Clone the repository
git clone https://github.com/your-repo/pharm-inventory.git

# Setup environment
cp .env.example .env
docker-compose up -d
```

## Documentation
- [API Documentation](/docs/api.md)
- [Architecture Overview](/docs/architecture.md)
- [Development Guide](/docs/development.md)

## MCP server

This repo now includes an Inventory Service MCP server at `mcp_server.server`.

Current tools:

- `search_inventories`: search inventory ledgers scoped to the authenticated caller's workspace
- `get_inventory_details`: inspect a single inventory ledger with current stock posture
- `search_stock_items`: search inventory item records with current stock status
- `get_inventory_item_details`: inspect lots, serials, reservations, and movements for one inventory item
- `get_inventory_alerts`: surface low-stock, reorder, out-of-stock, and expiring inventory queues
- `search_stock_locations`: search stock locations with summarized quantity and value posture
- `get_stock_location_summary`: inspect one location's stock concentration and expiry posture
- `search_stock_reservations`: inspect stock reservations by order, lot, serial, or location
- `search_stock_movements`: inspect stock movements by item, reference, lot, serial, or location
- `get_stock_analytics`: workspace-wide stock analytics across value, locations, and aging

Transport:

- Streamable HTTP endpoint: `/mcp`
- Health endpoint: `/health`

Local Docker Compose service:

- Service name: `inventory_mcp`
- Container name: `inventory-mcp`
- Host port: `7020`

Run locally:

```bash
docker compose up inventory_mcp
```

Direct local run:

```bash
uv run python -m mcp_server.server
```

Relevant environment variables:

- `INVENTORY_MCP_HOST` default `0.0.0.0`
- `INVENTORY_MCP_PORT` default `8000`
- `INVENTORY_MCP_MOUNT_PATH` default `/mcp`
- `INVENTORY_MCP_LOG_LEVEL` default `info`
- `INVENTORY_MCP_ALLOWED_HOSTS` optional comma-separated Host allowlist for FastMCP transport security
- `INVENTORY_MCP_ALLOWED_ORIGINS` optional comma-separated Origin allowlist for FastMCP transport security

Authentication:

- The MCP server expects the same Bearer access token issued by `intera_users`.
- Authenticated tools require `user_id` and `profile_id` claims.

Recommended K-A2A config for this MCP server:

```json
{
  "id": "inventory",
  "serverUrl": "http://inventory-mcp:8000/mcp/",
  "auth": { "mode": "forward_bearer" },
  "tools": [
    "search_inventories",
    "get_inventory_details",
    "search_stock_items",
    "get_inventory_item_details",
    "get_inventory_alerts",
    "search_stock_locations",
    "get_stock_location_summary",
    "search_stock_reservations",
    "search_stock_movements",
    "get_stock_analytics"
  ]
}
```

## Technology Stack
- **Backend**: Django, Django REST Framework
- **Database**: PostgreSQL
- **Cache**: Redis
- **Frontend**: HTML templates with Bootstrap
- **Deployment**: Docker

## License
MIT
