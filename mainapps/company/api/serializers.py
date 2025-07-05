from rest_framework import serializers
from ..models import Company, CompanyAddress, Contact


class CompanyAddressSerializer(serializers.ModelSerializer):
    full_address=serializers.SerializerMethodField()
    class Meta:
        model = CompanyAddress
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')
    def get_full_address(self, obj):
        return f"{obj.apt_number}, {obj.street_number}, {obj.street}, {obj.region},"
        return

class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = '__all__'
        read_only_fields = ['id']
    
class CompanySerializer(serializers.ModelSerializer):
    company_type=serializers.SerializerMethodField()
    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'website', 'phone', 'email', 'link',
            'is_customer', 'is_supplier', 'is_manufacturer', 'currency',
            
            'company_type','short_address',
        ]
        read_only_fields = ['id','company_type']
    def get_company_type(self, obj):
        company_type=[]
        if obj.is_customer:
             company_type.append('Customer')
        if obj.is_supplier:
            company_type.append('Supplier')
        if obj.is_manufacturer:
            company_type.append('Manufacturer')
        return ','.join(company_type)
        