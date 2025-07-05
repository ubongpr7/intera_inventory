"""
POS API Documentation

This module provides the API endpoints for the Point of Sale system.

Endpoints:
---------

Products:
- GET /pos_api/products/ - List all products
- GET /pos_api/products/{id}/ - Get product details
- GET /pos_api/products/popular/ - List popular products
- GET /pos_api/products/?category={category} - Filter products by category

Customers:
- GET /pos_api/customers/ - List all customers
- GET /pos_api/customers/{id}/ - Get customer details

Tables:
- GET /pos_api/tables/ - List all tables
- GET /pos_api/tables/{id}/ - Get table details
- PATCH /pos_api/tables/{id}/status/ - Update table status

Orders:
- GET /pos_api/orders/ - List all orders
- GET /pos_api/orders/active/ - List active orders
- GET /pos_api/orders/{id}/ - Get order details
- POST /pos_api/orders/ - Create a new order
- PATCH /pos_api/orders/{id}/status/ - Update order status

Payments:
- POST /pos_api/payments/ - Process a payment

Sessions:
- POST /pos_api/sessions/ - Start a new session
- GET /pos_api/sessions/{id}/ - Get session details
- POST /pos_api/sessions/{id}/close/ - Close a session

Sync:
- POST /pos_api/sync/sync_pending/ - Sync pending operations
"""
