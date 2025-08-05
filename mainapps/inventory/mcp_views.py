from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Inventory
from .api.serializers import InventoryListSerializer

class InventoryListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all inventories.

    This tool allows an AI agent to fetch a comprehensive list of all available inventory records.
    It is useful for getting an overview of all inventories in the system.

    Parameters:
    - None

    Returns:
    - A list of inventory objects, each containing details such as name, location, and timestamps.
    """
    queryset = Inventory.objects.all()
    serializer_class = InventoryListSerializer

class InventoryCreateAPIView(generics.CreateAPIView):
    """
    Creates a new inventory record.

    This tool enables an AI agent to add a new inventory to the system.
    It requires providing the necessary details for the new inventory.

    Parameters (in request body):
    - `name` (string, required): The name of the inventory.
    - `location` (string, optional): The physical location of the inventory.

    Returns:
    - The newly created inventory object with its assigned ID.
    """
    queryset = Inventory.objects.all()
    serializer_class = InventoryListSerializer

class InventoryRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single inventory by its ID.

    This tool allows an AI agent to get detailed information about a specific inventory.
    It is useful for inspecting a particular inventory's attributes.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the inventory.

    Returns:
    - A single inventory object containing its name, location, and timestamps.
    """
    queryset = Inventory.objects.all()
    serializer_class = InventoryListSerializer

class InventoryUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing inventory record by its ID.

    This tool enables an AI agent to modify the details of an existing inventory.
    Only the fields provided in the request body will be updated.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the inventory to update.

    Parameters (in request body):
    - `name` (string, optional): The new name for the inventory.
    - `location` (string, optional): The new location for the inventory.

    Returns:
    - The updated inventory object.
    """
    queryset = Inventory.objects.all()
    serializer_class = InventoryListSerializer

class InventoryDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes an inventory record by its ID.

    This tool allows an AI agent to remove an inventory from the system.
    Use with caution, as this action is irreversible.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the inventory to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = Inventory.objects.all()
    serializer_class = InventoryListSerializer
