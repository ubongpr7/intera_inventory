# views.py
from rest_framework import viewsets, status
from rest_framework.response import Response

from mainapps.inventory.api.views import BaseInventoryViewSet
from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, PermissionRequiredMixin
from ..models import Company, CompanyAddress, Contact
from .serializers import CompanyAddressSerializer, CompanySerializer, ContactSerializer
from rest_framework.decorators import action

class CompanyViewSet(BaseInventoryViewSet):
    serializer_class = CompanySerializer
    queryset=Company.objects.all()
    # required_permission=UNIFIED_PERMISSION_DICT.get('company')

    @action(methods=['GET'], detail=True)
    def addresses(self, request, pk=None):
        company = self.get_object()
        addressses = CompanyAddress.objects.filter(company=company)    
        serializer= CompanyAddressSerializer(addressses, many= True)
        return Response(serializer.data)    

    @action(methods=['GET'], detail=True)
    def contacts(self, request, pk=None):
        company = self.get_object()
        contacts = Contact.objects.filter(company=company)    
        serializer= ContactSerializer(contacts, many= True)
        return Response(serializer.data)    

    
class CompanyAddressViewSet(BaseCachePermissionViewset):
    serializer_class = CompanyAddressSerializer
    queryset=CompanyAddress.objects.all()

    
class ContactPersonViewSet(BaseCachePermissionViewset):
    serializer_class = ContactSerializer
    queryset= Contact.objects.all()
    