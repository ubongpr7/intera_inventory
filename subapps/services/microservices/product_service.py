import requests
import logging
from django.conf import settings
from django.core.cache import cache
from typing import Optional, Dict, Any

# Re-using UserService for authentication headers as per the previous pattern
from .user_service import UserService

logger = logging.getLogger(__name__)

class ProductService:
    """
    Service to interact with the Product microservice.
    """
    
    BASE_URL = getattr(settings, 'PRODUCT_SERVICE_URL', 'http://product-service:8000')
    CACHE_TIMEOUT = 300  # 5 minutes cache

    @classmethod
    def get_variant_details_by_barcode(cls, barcode: str, request) -> Optional[Dict[str, Any]]:
        """
        Fetches minimal product variant details, including the image, by its barcode.
        
        Args:
            barcode: The barcode of the product variant.
            request: The Django request object to extract authentication headers.

        Returns:
            A dictionary containing the variant details or None if not found or an error occurs.
        """
        logger.debug(f"Attempting to fetch variant details for barcode: {barcode}")
        if not barcode:
            logger.debug("Barcode is empty, returning None.")
            return None

        cache_key = f"product_variant_details_{barcode}"
        cached_data = cache.get(cache_key)

        if cached_data:
            logger.debug(f"Returning cached data for barcode: {barcode}")
            return cached_data

        endpoint = "product_api/variants/minimal_details_barcode/"
        url = f"{cls.BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}/"
        
        params = {"barcode": barcode}
        
        logger.debug(f"Making API call to {url} with params: {params}")
        try:
            response = requests.get(
                url,
                params=params,
                headers=UserService.get_auth_header(request),
                timeout=5
            )

            if response.status_code == 200:
                data = response.json()
                if data:
                    cache.set(cache_key, data, cls.CACHE_TIMEOUT)
                    logger.info(f"Fetched and cached details for barcode: {barcode}")
                    logger.debug(f"Successfully fetched data for barcode {barcode}: {data}")
                    return data
                else:
                    logger.warning(f"No product variant found for barcode: {barcode}")
                    logger.debug(f"API returned no data for barcode {barcode}.")
                    return None
            else:
                logger.error(
                    f"Failed to fetch variant details for barcode {barcode}. "
                    f"Status: {response.status_code}, Response: {response.text[:200]}"
                )
                logger.debug(f"API call failed with status {response.status_code} for barcode {barcode}.")
                return None

        except requests.RequestException as e:
            logger.error(f"Error calling product service for barcode {barcode}: {str(e)}")
            logger.debug(f"Exception occurred during API call for barcode {barcode}: {e}")
            return None