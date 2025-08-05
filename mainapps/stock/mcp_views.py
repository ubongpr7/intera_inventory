from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import StockLocation, StockItem, StockAdjustment
from .api.serializers import StockLocationListSerializer, StockItemSerializer, StockAdjustmentSerializer
from rest_framework.exceptions import ValidationError

class StockLocationListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all stock locations.

    This tool allows an AI agent to fetch a comprehensive list of all physical or logical locations
    where stock is stored. It is useful for understanding the distribution of inventory.

    Parameters:
    - None

    Returns:
    - A list of stock location objects, each containing details such as name, address, and active status.
    """
    queryset = StockLocation.objects.all()
    serializer_class = StockLocationListSerializer

class StockLocationCreateAPIView(generics.CreateAPIView):
    """
    Creates a new stock location.

    This tool enables an AI agent to add a new storage location for stock.

    Parameters (in request body):
    - `name` (string, required): The unique name of the stock location.
    - `address` (string, optional): The physical address of the location.
    - `is_active` (boolean, optional): Whether the location is currently active (defaults to true).

    Returns:
    - The newly created stock location object with its assigned ID.
    """
    queryset = StockLocation.objects.all()
    serializer_class = StockLocationListSerializer

class StockLocationRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single stock location by its ID.

    This tool allows an AI agent to get detailed information about a specific stock location.
    It is useful for inspecting a particular location's attributes.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock location.

    Returns:
    - A single stock location object containing its name, address, and active status.
    """
    queryset = StockLocation.objects.all()
    serializer_class = StockLocationListSerializer

class StockLocationUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing stock location by its ID.

    This tool enables an AI agent to modify the details of an existing stock location.
    Only the fields provided in the request body will be updated.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock location to update.

    Parameters (in request body):
    - `name` (string, optional): The new unique name for the location.
    - `address` (string, optional): The new address for the location.
    - `is_active` (boolean, optional): The new active status for the location.

    Returns:
    - The updated stock location object.
    """
    queryset = StockLocation.objects.all()
    serializer_class = StockLocationListSerializer

class StockLocationDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes a stock location by its ID.

    This tool allows an AI agent to remove a stock location from the system.
    Use with caution, as this action is irreversible and may affect associated stock items.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock location to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = StockLocation.objects.all()
    serializer_class = StockLocationListSerializer

class StockItemListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all stock items.

    This tool allows an AI agent to fetch a comprehensive list of all individual stock items
    currently in various locations. It is useful for getting an overview of all products in stock.

    Parameters:
    - None

    Returns:
    - A list of stock item objects, each containing details such as product ID, quantity, and location.
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer

class StockItemCreateAPIView(generics.CreateAPIView):
    """
    Creates a new stock item record.

    This tool enables an AI agent to add a new quantity of a product to a specific stock location.
    It requires providing the product ID, location ID, and quantity.

    Parameters (in request body):
    - `product_id` (string, required): The identifier of the product.
    - `location` (integer, required): The ID of the stock location where the item is stored.
    - `quantity` (integer, required): The quantity of the product at this location.

    Returns:
    - The newly created stock item object with its assigned ID.
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer

class StockItemRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single stock item by its ID.

    This tool allows an AI agent to get detailed information about a specific stock item.
    It is useful for inspecting a particular item's quantity and location.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock item.

    Returns:
    - A single stock item object containing its product ID, quantity, and location.
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer

class StockItemUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing stock item record by its ID.

    This tool enables an AI agent to modify the details of an existing stock item,
    such as its quantity or location.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock item to update.

    Parameters (in request body):
    - `product_id` (string, optional): The new identifier of the product.
    - `location` (integer, optional): The new ID of the stock location.
    - `quantity` (integer, optional): The new quantity of the product.

    Returns:
    - The updated stock item object.
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer

class StockItemDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes a stock item record by its ID.

    This tool allows an AI agent to remove a specific stock item from the system.
    Use with caution, as this action is irreversible.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock item to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer

class StockAdjustmentListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all stock adjustments.

    This tool allows an AI agent to fetch a comprehensive list of all historical adjustments
    made to stock items. It is useful for auditing stock movements.

    Parameters:
    - None

    Returns:
    - A list of stock adjustment objects, each containing details such as adjustment type,
      quantity change, reason, and the associated stock item.
    """
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer

class StockAdjustmentCreateAPIView(generics.CreateAPIView):
    """
    Creates a new stock adjustment record.

    This tool enables an AI agent to record a change in stock quantity for a specific item.
    It requires the stock item ID, adjustment type, and quantity change.

    Parameters (in request body):
    - `stock_item` (integer, required): The ID of the stock item being adjusted.
    - `adjustment_type` (string, required): The type of adjustment ('add', 'remove', 'transfer').
    - `quantity_change` (integer, required): The amount by which the quantity changed (can be negative for 'remove').
    - `reason` (string, optional): A description of why the adjustment was made.
    - `adjusted_by` (string, optional): The name of the person or system making the adjustment.

    Returns:
    - The newly created stock adjustment object with its assigned ID.
    """
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer

class StockAdjustmentRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single stock adjustment by its ID.

    This tool allows an AI agent to get detailed information about a specific stock adjustment.
    It is useful for inspecting a particular adjustment's details.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock adjustment.

    Returns:
    - A single stock adjustment object containing its type, quantity change, reason, and associated stock item.
    """
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer

class StockAdjustmentUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing stock adjustment record by its ID.

    This tool enables an AI agent to modify the details of an existing stock adjustment.
    Only the fields provided in the request body will be updated.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock adjustment to update.

    Parameters (in request body):
    - `stock_item` (integer, optional): The ID of the stock item being adjusted.
    - `adjustment_type` (string, optional): The new type of adjustment.
    - `quantity_change` (integer, optional): The new quantity change.
    - `reason` (string, optional): The new reason for the adjustment.
    - `adjusted_by` (string, optional): The new name of the person or system making the adjustment.

    Returns:
    - The updated stock adjustment object.
    """
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer

class StockAdjustmentDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes a stock adjustment record by its ID.

    This tool allows an AI agent to remove a specific stock adjustment from the system.
    Use with caution, as this action is irreversible.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the stock adjustment to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer
