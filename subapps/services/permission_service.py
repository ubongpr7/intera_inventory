import requests
import logging
from django.conf import settings
from django.core.cache import cache
from typing import Optional, Dict, Any, Set, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class PermissionService:
    """Service for handling permissions in microservice architecture"""
    
    USER_SERVICE_URL = getattr(settings, 'USER_SERVICE_URL', 'http://localhost:8000')
    CACHE_TIMEOUT = 300  # 5 minutes
    
    @classmethod
    def get_user_permissions(cls, user_id: str, profile_id: str = None) -> Set[str]:
        """Get all permissions for a user from the user microservice"""
        if not user_id:
            return set()
        
        # Create cache key
        cache_key = f"user_permissions_{user_id}_{profile_id or 'default'}"
        cached_permissions = cache.get(cache_key)
        
        if cached_permissions is not None:
            return set(cached_permissions)
        
        try:
            # Call user microservice to get permissions
            response = requests.get(
                f"{cls.USER_SERVICE_URL}/account_api/users/{user_id}/permissions/",
                params={'profile_id': profile_id} if profile_id else {},
                timeout=5
            )
            
            if response.status_code == 200:
                permission_data = response.json()
                permissions = cls._extract_permissions_from_response(permission_data)
                
                # Cache the permissions
                cache.set(cache_key, list(permissions), cls.CACHE_TIMEOUT)
                return permissions
            else:
                logger.warning(f"Permission service returned {response.status_code} for user {user_id}")
                return set()
                
        except requests.RequestException as e:
            logger.error(f"Error fetching permissions for user {user_id}: {str(e)}")
            return set()
    
    @classmethod
    def _extract_permissions_from_response(cls, permission_data: Dict) -> Set[str]:
        """Extract permissions from the user service response"""
        permissions = set()
        
        # Direct user permissions
        user_permissions = permission_data.get('custom_permissions', [])
        permissions.update([perm.get('codename') for perm in user_permissions if perm.get('codename')])
        
        # Role-based permissions
        roles = permission_data.get('roles', [])
        for role in roles:
            # Check if role is still active
            if cls._is_role_active(role):
                role_permissions = role.get('role', {}).get('permissions', [])
                permissions.update([perm.get('codename') for perm in role_permissions if perm.get('codename')])
        
        # Group permissions
        groups = permission_data.get('staff_groups', [])
        for group in groups:
            group_permissions = group.get('permissions', [])
            permissions.update([perm.get('codename') for perm in group_permissions if perm.get('codename')])
        
        return permissions
    
    @classmethod
    def _is_role_active(cls, role: Dict) -> bool:
        """Check if a role is still active based on dates"""
        start_date = role.get('start_date')
        end_date = role.get('end_date')
        
        if not start_date or not end_date:
            return True
        
        try:
            from django.utils import timezone
            from django.utils.dateparse import parse_datetime
            
            current_time = timezone.now()
            start_dt = parse_datetime(start_date) if isinstance(start_date, str) else start_date
            end_dt = parse_datetime(end_date) if isinstance(end_date, str) else end_date
            
            return start_dt <= current_time <= end_dt
        except Exception as e:
            logger.error(f"Error checking role activity: {e}")
            return False
    
    @classmethod
    def check_user_is_owner(cls, user_id: str, profile_id: str) -> bool:
        """Check if user is the owner of the profile/company"""
        if not user_id or not profile_id:
            return False
        
        cache_key = f"user_owner_{user_id}_{profile_id}"
        cached_result = cache.get(cache_key)
        
        if cached_result is not None:
            return cached_result
        
        try:
            response = requests.get(
                f"{cls.USER_SERVICE_URL}/account_api/profiles/{profile_id}/owner/",
                timeout=5
            )
            
            if response.status_code == 200:
                owner_data = response.json()
                is_owner = owner_data.get('owner_id') == user_id
                
                # Cache for shorter time since ownership rarely changes
                cache.set(cache_key, is_owner, 600)  # 10 minutes
                return is_owner
            
            return False
            
        except requests.RequestException as e:
            logger.error(f"Error checking ownership for user {user_id}: {str(e)}")
            return False
    
    @classmethod
    def invalidate_user_permissions_cache(cls, user_id: str, profile_id: str = None):
        """Invalidate cached permissions for a user"""
        cache_key = f"user_permissions_{user_id}_{profile_id or 'default'}"
        cache.delete(cache_key)
        
        # Also invalidate ownership cache
        if profile_id:
            owner_cache_key = f"user_owner_{user_id}_{profile_id}"
            cache.delete(owner_cache_key)
    
    @classmethod
    def bulk_check_permissions(cls, user_id: str, permissions: List[str], profile_id: str = None) -> Dict[str, bool]:
        """Check multiple permissions at once for efficiency"""
        user_permissions = cls.get_user_permissions(user_id, profile_id)
        
        return {
            permission: permission in user_permissions
            for permission in permissions
        }
