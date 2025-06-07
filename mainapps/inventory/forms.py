from django import forms
from .models import Inventory,InventoryCategory


class InVentoryForm(forms.ModelForm):
    category=forms.ModelChoiceField(
        queryset=InventoryCategory.objects.all(),
        widget=forms.Select(
            attrs={
                "hx-get": '/inventory/get-inventory-categories/',
                "hx-target":'#id_category',
                'hx-trigger':'revealed'
                }
            )
    )
    
    
    class Meta:
        model = Inventory
        fields = '__all__'


class InventoryCategoryForm(forms.ModelForm):
    
    parent=forms.ModelChoiceField(
        queryset=InventoryCategory.objects.all(),
        label='Parent category',
        required=False,
        widget=forms.Select(
            attrs={
                "hx-get": '/inventory/get-inventory-categories/',
                "hx-target":'#id_parent',
                'hx-trigger':'revealed'

                }
            )
    )
    
        
    # )
    class Meta:
        model = InventoryCategory
        exclude = ("parent",)

        # fields=['parent']
