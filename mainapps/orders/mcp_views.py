from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from mainapps.company.mcp_views import BaseInventoryViewSet
from .models import PurchaseOrder, SalesOrder, PurchaseOrderLineItem
from .api.serializers import PurchaseOrderDetailSerializer, SalesOrderSerializer, PurchaseOrderLineItemSerializer
from rest_framework.exceptions import ValidationError

class PurchaseOrderListAPIView(BaseInventoryViewSet,generics.ListAPIView):
    """
    Retrieves a list of all purchase orders.

    This tool allows an AI agent to fetch a comprehensive list of all purchase orders
    made to suppliers. It is useful for tracking incoming inventory.

    Parameters:
    - None

    Returns:
    - A list of purchase order objects, each containing details such as supplier name,
      order date, expected delivery date, status, and total amount.
    """
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderDetailSerializer

class PurchaseOrderCreateAPIView(BaseInventoryViewSet,generics.CreateAPIView):
    """
    Creates a new purchase order.

    This tool enables an AI agent to generate a new order for goods from a supplier.

    Parameters (in request body):
    - `supplier_name` (string, required): The name of the supplier.
    - `expected_delivery_date` (date, optional): The anticipated date for delivery.
    - `status` (string, optional): The initial status of the order (e.g., 'pending', 'approved').
    - `total_amount` (decimal, optional): The total cost of the order.

    Returns:
    - The newly created purchase order object with its assigned ID.
    """
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderDetailSerializer

class PurchaseOrderRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single purchase order by its ID.

    This tool allows an AI agent to get detailed information about a specific purchase order.
    It is useful for inspecting a particular order's status and items.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the purchase order.

    Returns:
    - A single purchase order object containing its details.
    """
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderDetailSerializer

class PurchaseOrderUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing purchase order by its ID.

    This tool enables an AI agent to modify the details of an existing purchase order,
    such as its status or expected delivery date.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the purchase order to update.

    Parameters (in request body):
    - `supplier_name` (string, optional): The new supplier name.
    - `expected_delivery_date` (date, optional): The new expected delivery date.
    - `status` (string, optional): The new status of the order.

    Returns:
    - The updated purchase order object.
    """
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderDetailSerializer

class PurchaseOrderDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes a purchase order by its ID.

    This tool allows an AI agent to cancel or remove a purchase order from the system.
    Use with caution, as this action is irreversible.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the purchase order to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderDetailSerializer

class SalesOrderListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all sales orders.

    This tool allows an AI agent to fetch a comprehensive list of all sales orders
    from customers. It is useful for tracking outgoing inventory and customer demand.

    Parameters:
    - None

    Returns:
    - A list of sales order objects, each containing details such as customer name,
      order date, delivery date, status, and total amount.
    """
    queryset = SalesOrder.objects.all()
    serializer_class = SalesOrderSerializer

class SalesOrderCreateAPIView(generics.CreateAPIView):
    """
    Creates a new sales order.

    This tool enables an AI agent to generate a new order for goods from a customer.

    Parameters (in request body):
    - `customer_name` (string, required): The name of the customer.
    - `delivery_date` (date, optional): The anticipated date for delivery to the customer.
    - `status` (string, optional): The initial status of the order (e.g., 'pending', 'shipped').
    - `total_amount` (decimal, optional): The total cost of the order.

    Returns:
    - The newly created sales order object with its assigned ID.
    """
    queryset = SalesOrder.objects.all()
    serializer_class = SalesOrderSerializer

class SalesOrderRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single sales order by its ID.

    This tool allows an AI agent to get detailed information about a specific sales order.
    It is useful for inspecting a particular order's status and items.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the sales order.

    Returns:
    - A single sales order object containing its details.
    """
    queryset = SalesOrder.objects.all()
    serializer_class = SalesOrderSerializer

class SalesOrderUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing sales order by its ID.

    This tool enables an AI agent to modify the details of an existing sales order,
    such as its status or delivery date.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the sales order to update.

    Parameters (in request body):
    - `customer_name` (string, optional): The new customer name.
    - `delivery_date` (date, optional): The new delivery date.
    - `status` (string, optional): The new status of the order.
    - `total_amount` (decimal, optional): The new total amount.

    Returns:
    - The updated sales order object.
    """
    queryset = SalesOrder.objects.all()
    serializer_class = SalesOrderSerializer

class SalesOrderDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes a sales order by its ID.

    This tool allows an AI agent to cancel or remove a sales order from the system.
    Use with caution, as this action is irreversible.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the sales order to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = SalesOrder.objects.all()
    serializer_class = SalesOrderSerializer

class PurchaseOrderLineItemListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all order items.

    This tool allows an AI agent to fetch a comprehensive list of all individual items
    within purchase or sales orders. It is useful for seeing what products are part of orders.

    Parameters:
    - None

    Returns:
    - A list of order item objects, each containing details such as product ID, quantity,
      unit price, total price, and associated order.
    """
    queryset = PurchaseOrderLineItem.objects.all()
    serializer_class = PurchaseOrderLineItemSerializer

class PurchaseOrderLineItemCreateAPIView(generics.CreateAPIView):
    """
    Creates a new order item.

    This tool enables an AI agent to add a new item to an existing purchase or sales order.
    It requires either a `purchase_order` ID or a `sales_order` ID, along with product details.

    Parameters (in request body):
    - `purchase_order` (string): The ID of the purchase order this item belongs to.
    - `stock_item` (string, required): The identifier of the product.
    - `quantity` (integer, required): The quantity of the product.
    - `unit_price` (decimal, required): The unit price of the product.

    Returns:
    - The newly created order item object with its assigned ID.
    """
    queryset = PurchaseOrderLineItem.objects.all()
    serializer_class = PurchaseOrderLineItemSerializer


class PurchaseOrderLineOrderItemUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing order item by its ID.

    This tool enables an AI agent to modify the details of an existing order item,
    such as its quantity or unit price.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the order item to update.

    Parameters (in request body):
    - `stock_item` (string, optional): The new identifier of the product.
    - `quantity` (integer, optional): The new quantity of the product.
    - `unit_price` (decimal, optional): The new unit price of the product.

    Returns:
    - The updated order item object.
    """

    queryset = PurchaseOrderLineItem.objects.all()
    serializer_class = PurchaseOrderLineItemSerializer

class PurchaseOrderLineItemDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes an order item by its ID.

    This tool allows an AI agent to remove a specific item from a purchase or sales order.
    Use with caution, as this action is irreversible.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the order item to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = PurchaseOrderLineItem.objects.all()
    serializer_class = PurchaseOrderLineItemSerializer
