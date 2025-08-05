from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from company.models import Company, Branch, Department
from .api.serializers import CompanySerializer
from rest_framework.exceptions import ValidationError

class CompanyListAPIView(generics.ListAPIView):
    """
    Retrieves a list of all companies.

    This tool allows an AI agent to fetch a comprehensive list of all registered companies.
    It is useful for getting an overview of all organizational entities.

    Parameters:
    - None

    Returns:
    - A list of company objects, each containing details such as name, address, and contact information.
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

class CompanyCreateAPIView(generics.CreateAPIView):
    """
    Creates a new company record.

    This tool enables an AI agent to add a new company to the system.
    It requires providing the unique name for the new company.

    Parameters (in request body):
    - `name` (string, required): The unique name of the company.
    - `address` (string, optional): The main address of the company.
    - `phone_number` (string, optional): The contact phone number.
    - `email` (string, optional): The contact email address.
    - `website` (string, optional): The company's website URL.
    
    Returns:
    - The newly created company object with its assigned ID.
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

class CompanyRetrieveAPIView(generics.RetrieveAPIView):
    """
    Retrieves a single company by its ID.

    This tool allows an AI agent to get detailed information about a specific company.
    It is useful for inspecting a particular company's attributes.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the company.

    Returns:
    - A single company object containing its name, address, and contact information.
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

class CompanyUpdateAPIView(generics.UpdateAPIView):
    """
    Updates an existing company record by its ID.

    This tool enables an AI agent to modify the details of an existing company.
    Only the fields provided in the request body will be updated.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the company to update.

    Parameters (in request body):
    - `name` (string, optional): The new unique name for the company.
    - `address` (string, optional): The new address.
    - `phone_number` (string, optional): The new phone number.
    - `email` (string, optional): The new email.
    - `website` (string, optional): The new website.

    Returns:
    - The updated company object.
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

class CompanyDestroyAPIView(generics.DestroyAPIView):
    """
    Deletes a company record by its ID.

    This tool allows an AI agent to remove a company from the system.
    Use with caution, as this action is irreversible and may affect associated branches and departments.

    Parameters (in URL path):
    - `pk` (integer, required): The unique identifier of the company to delete.

    Returns:
    - An empty response with a 204 No Content status on successful deletion.
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
