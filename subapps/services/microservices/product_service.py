import requests
import logging
from django.conf import settings
from django.core.cache import cache
from typing import Optional, Dict, Any, List
from decimal import Decimal

from mainapps.product.models import ProductVariant
from .user_service import UserService

logger = logging.getLogger(__name__)

class InventoryService:
    """Enhanced service for inventory microservice with POS features"""
    
    BASE_URL = getattr(settings, 'INVENTORY_SERVICE_URL', 'http://inventory-service:8000')
    CACHE_TIMEOUT = 300
    STOCK_ITEMS_ENDPOINT = 'stock_api/stock-items/'
    STOCK_MOVEMENTS_ENDPOINT = 'stock_api/stock-movements/'

    @classmethod
    def get_bulk_stock_info(cls, variant_ids: List[str],request ) -> Dict[str, Dict]:
        """Get stock info for multiple variants in one request"""
        cache_keys = [f"stock_item_{vid}" for vid in variant_ids]
        cached_data = cache.get_many(cache_keys)
        
        # Find missing data
        missing_ids = [
            vid for vid, cache_key in zip(variant_ids, cache_keys) 
            if cache_key not in cached_data
        ]
        
        result = {}
        
        # Add cached data
        for vid, cache_key in zip(variant_ids, cache_keys):
            if cache_key in cached_data:
                result[vid] = cached_data[cache_key]
        
        # Fetch missing data
        if missing_ids:
            try:
                response = requests.post(
                    f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}bulk/",
                    json={"variant_ids": missing_ids},
                    headers=UserService.get_authentication_credentials(request),
                    timeout=10
                )
                
                if response.status_code == 200:
                    bulk_data = response.json()
                    
                    # Cache and add to result
                    cache_data = {}
                    for item in bulk_data:
                        vid = item.get('product_variant')
                        if vid:
                            result[vid] = item
                            cache_data[f"stock_item_{vid}"] = item
                    
                    cache.set_many(cache_data, cls.CACHE_TIMEOUT)
                    
            except requests.RequestException as e:
                logger.error(f"Error fetching bulk stock data: {str(e)}")
        
        return result

    @classmethod
    def update_stock_quantity(cls, variant_id: str, request, new_quantity: float) -> bool:
        """Update stock quantity for a variant"""
        try:
            response = requests.patch(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}{variant_id}/",
                json={"quantity": str(new_quantity)},
                headers=UserService.get_authentication_credentials(request),
                timeout=5
            )
            
            if response.status_code == 200:
                # Clear cache
                cache.delete(f"stock_item_{variant_id}")
                return True
            else:
                logger.error(f"Failed to update stock for {variant_id}: {response.status_code}")
                return False
                
        except requests.RequestException as e:
            logger.error(f"Error updating stock for {variant_id}: {str(e)}")
            return False

    @classmethod
    def reserve_stock(cls, variant_id: str,request,  quantity: float, reference: str) -> bool:
        """Reserve stock for POS transaction"""
        try:
            response = requests.post(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}{variant_id}/reserve/",
                json={
                    "quantity": str(quantity),
                    "reference": reference,
                    "type": "pos_sale"
                },
                headers=UserService.get_authentication_credentials(request),
                timeout=5
            )
            
            if response.status_code == 200:
                # Clear cache
                cache.delete(f"stock_item_{variant_id}")
                return True
            return False
            
        except requests.RequestException as e:
            logger.error(f"Error reserving stock for {variant_id}: {str(e)}")
            return False

    @classmethod
    def release_stock_reservation(cls, variant_id: str,request, reference: str) -> bool:
        """Release stock reservation"""
        try:
            response = requests.post(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}{variant_id}/release/",
                json={"reference": reference},
                headers=UserService.get_authentication_credentials(request),
                timeout=5
            )
            
            if response.status_code == 200:
                cache.delete(f"stock_item_{variant_id}")
                return True
            return False
            
        except requests.RequestException as e:
            logger.error(f"Error releasing stock reservation for {variant_id}: {str(e)}")
            return False

    @classmethod
    def get_low_stock_alerts(cls, request, threshold: int = 10) -> List[Dict]:
        """Get products with low stock for POS alerts"""
        cache_key = f"low_stock_alerts_{request.headers.get('X-Profile-ID')}_{threshold}"
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return cached_data
        
        try:
            response = requests.get(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}low-stock/",
                params={"threshold": threshold},
                headers=UserService.get_authentication_credentials(request),

                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                cache.set(cache_key, data, 60)  # Cache for 1 minute
                return data
            return []
            
        except requests.RequestException as e:
            logger.error(f"Error fetching low stock alerts: {str(e)}")
            return []

    # Keep existing methods with enhancements
    @classmethod
    def create_stock_item(cls,  request, variant) -> Optional[Dict[str, Any]]:
        """Enhanced stock item creation"""
        from decimal import Decimal

        payload = {
            "created_by": request.user.id,
            "name": f"{variant.pos_name}",
            "product_variant": str(variant.id),
            "inventory": str(variant.product.inventory),
            "quantity":'0.0',
            "status": "ok",
            "delete_on_deplete": False,
        }
        
        try:
            response = requests.post(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}",
                json=payload,
                headers=UserService.get_authentication_credentials(request),
                timeout=10  # Increased timeout for creation
            )
            
            if response.status_code == 201:
                stock_item_data = response.json()
                cache_key = f"stock_item_{variant.id}"
                cache.set(cache_key, stock_item_data, cls.CACHE_TIMEOUT)
                logger.info(f"Created StockItem for variant {variant.id}")
                print(stock_item_data.get('sku'))
                variant.variant_sku=stock_item_data.get('sku')
                variant.save()

                return stock_item_data
            else:
                logger.error(f"Failed to create StockItem: {response.status_code} - {response.text}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Error creating StockItem for variant : {str(e)}")
            return None
        
    @classmethod
    def get_or_create_stock_item(cls, request, variant) -> Optional[Dict[str, Any]]:
        payload = {
            "created_by": request.user.id,
            "name": f"{variant.pos_display_name}",
            "product_variant": str(variant.id),
            "inventory": str(variant.product.inventory),
            "quantity": '0.0',
            "status": "ok",
            "delete_on_deplete": False,
        }
        
        endpoint = f"{cls.STOCK_ITEMS_ENDPOINT}create_for_variants/"
        url = f"{cls.BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
        
        try:
            # 2. Disable redirects and add explicit timeout
            response = requests.post(
                url,
                json=payload,
                headers=UserService.get_authentication_credentials(request),
                timeout=(3.05, 10),  # Connect + read timeout
                allow_redirects=False  # Critical for POST requests
            )
            
            # 3. Handle successful response
            if response.status_code in (200, 201):
                stock_item_data = response.json()
                # ... (caching and variant update logic)
                return stock_item_data
            
            # 4. Log detailed error on failure
            logger.error(
                f"Failed to create StockItem. "
                f"Status: {response.status_code}, "
                f"Response: {response.text[:500]}, "
                f"URL: {url}"
            )
            return None
            
        except requests.RequestException as e:
            logger.error(f"Request failed: {str(e)} URL: {url}")
            return None
            
    @classmethod
    def create_stock_item_via_command(cls,  created_by, variant,AUTHORIZATION) -> Optional[Dict[str, Any]]:
        """Enhanced stock item creation"""
        from decimal import Decimal

        payload = {
            "created_by": created_by,
            "name": f"Stock for Variant {variant.pos_name}",
            "product_variant": str(variant.id),
            "inventory": str(variant.product.inventory),
            "quantity":'0.0',
            "status": "ok",
            "delete_on_deplete": False,
        }
        
        try:
            response = requests.post(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}",
                json=payload,
                headers={'Authorization': AUTHORIZATION,'X-Profile-ID': variant.product.profile,},
                timeout=10  
            )
            
            if response.status_code == 201:
                stock_item_data = response.json()
                cache_key = f"stock_item_{variant.id}"
                cache.set(cache_key, stock_item_data, cls.CACHE_TIMEOUT)
                logger.info(f"Created StockItem for variant {variant.id}")
                print(stock_item_data.get('sku'))
                variant.variant_sku=stock_item_data.get('sku')
                variant.save()

                return stock_item_data
            else:
                logger.error(f"Failed to create StockItem: {response.status_code} - {response.text}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Error creating StockItem for variant : {str(e)}")
            return None

    @classmethod
    def get_stock_item(cls, product_variant_id: str, request) -> Optional[Dict[str, Any]]:
        """Enhanced stock item retrieval with better caching"""
        cache_key = f"stock_item_{product_variant_id}"
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return cached_data
        
        try:
            response = requests.get(
                f"{cls.BASE_URL}/{cls.STOCK_ITEMS_ENDPOINT}",
                params={"product_variant": product_variant_id},
                headers=UserService.get_authentication_credentials(request),
                timeout=5
            )
            
            if response.status_code == 200:
                stock_items = response.json()
                if stock_items and isinstance(stock_items, list) and len(stock_items) > 0:
                    stock_item = stock_items[0]
                    cache.set(cache_key, stock_item, cls.CACHE_TIMEOUT)
                    return stock_item
                return None
            else:
                logger.warning(f"Inventory service returned {response.status_code} for variant {product_variant_id}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Error fetching StockItem for variant {product_variant_id}: {str(e)}")
            return None
