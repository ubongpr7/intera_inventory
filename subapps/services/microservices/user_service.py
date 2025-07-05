import requests
import logging
from django.conf import settings
from django.core.cache import cache
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class UserService:
    
    """Service for communicating with the user microservice"""
    
    BASE_URL = getattr(settings, 'USER_SERVICE_URL', 'http://user-service:8000')
    CACHE_TIMEOUT = 300  
    USERS_BASE_ENDPOINT='api/v1/accounts'
    
    @classmethod
    def get_user_details(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch user details from user microservice"""
        if not user_id:
            return None
            
        # Check cache first
        cache_key = f"user_details_{user_id}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data
        
        try:
            response = requests.get(
                f"{cls.BASE_URL}/{cls.USERS_BASE_ENDPOINT}/users/{user_id}/",
                timeout=5
            )
            
            if response.status_code == 200:
                user_data = response.json()
                # Cache the result
                cache.set(cache_key, user_data, cls.CACHE_TIMEOUT)
                return user_data
            else:
                logger.warning(f"User service returned {response.status_code} for user {user_id}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Error fetching user details for {user_id}: {str(e)}")
            return None
    
    @classmethod
    def get_current_user(cls, request) -> Optional[Dict[str, Any]]:
        """Get current user details from request"""
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            return None
            
        try:
            response = requests.get(
                f"{cls.BASE_URL}/auth-api/users/me/",
                headers={'Authorization': auth_header},
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            return None
            
        except requests.RequestException as e:
            logger.error(f"Error fetching current user: {str(e)}")
            return None
    
    @classmethod
    def get_minimal_user_data(cls, user_id: str) -> Dict[str, Any]:
        """Get minimal user data with fallback"""
        user_data = cls.get_user_details(user_id)
        if user_data:
            return {
                'id': user_data.get('id'),
                'email': user_data.get('email'),
                'first_name': user_data.get('first_name', ''),
                'last_name': user_data.get('last_name', ''),
                'full_name': f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
                'profile_image': user_data.get('profile_image'),
            }
        return {
            'id': user_id,
            'email': 'Unknown',
            'first_name': '',
            'last_name': '',
            'full_name': 'Unknown User',
            'profile_image': None,
        }
