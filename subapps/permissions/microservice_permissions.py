from rest_framework import permissions
import logging
from rest_framework.response import Response
import hashlib
from django.core.cache import cache
from functools import wraps
from rest_framework_simplejwt.tokens import UntypedToken
from rest_framework import status
from rest_framework import viewsets

logger = logging.getLogger(__name__)

class HasModelRequestPermission(permissions.BasePermission):
    """
    Microservice-adapted permission class that checks permissions via user service
    """
    def get_user_permissions(self, token_str):
        try:
            token= UntypedToken(token_str)
            return set(token.payload.get('permissions')),token.payload.get('owner_id')
        except Exception as e:
            return {},False 
    def has_permission(self, request, view):
        permission = getattr(view, 'required_permission', None)

        if not permission:
            return True
       
        
        
        """Check if user has required permissions"""
        auth_header=request.headers.get('Authorization')
        if auth_header:
            token_str=auth_header.split(' ')[1]
            user_permissions,owner_id=self.get_user_permissions(token_str)
                 
            if owner_id == request.user.id:
                return True
            
            if permission:
            
                if isinstance(permission, dict):
                    action = view.action
                    permission= permission.get(action)
                return permission in user_permissions
        return False
        
class PermissionRequiredMixin:
    """
    Mixin to add permission checking to views
    """
    required_permission = None
    permission_classes = [ HasModelRequestPermission]
    
class CachingMixin:
    """
    Caching mixin for DRF ViewSets with multi-tenant support
    """
    CACHE_ENABLED = True
    CACHE_TTL = 60 * 60  # 1 hour
    CACHE_VERSION_KEY = "{model_name}_cache_version"
    CACHE_KEY_PREFIX = "{model_name}_cache"
    INCLUDE_HEADERS_IN_KEY = ['X-Profile-ID']  # Ensure tenant-based cache separation
    INCLUDE_QUERY_PARAMS = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_cache_config()

    def _init_cache_config(self):
        """Initialize cache configuration based on model"""
        if hasattr(self, 'queryset') and self.queryset is not None:
            model_name = self.queryset.model.__name__.lower()
        else:
            model_name = 'default'
        self.CACHE_VERSION_KEY = self.CACHE_VERSION_KEY.format(model_name=model_name)
        self.CACHE_KEY_PREFIX = self.CACHE_KEY_PREFIX.format(model_name=model_name)

    def _generate_cache_key(self, request, *args, **kwargs):
        """
        Generate unique cache key based on request, tenant (X-Profile-ID), and view specifics
        """
        path = request.path
        version = cache.get(self.CACHE_VERSION_KEY, 1)

        headers = {
            h: request.headers.get(h)
            for h in self.INCLUDE_HEADERS_IN_KEY
            if request.headers.get(h)
        }

        # Get query params if enabled
        params = {}
        if self.INCLUDE_QUERY_PARAMS and hasattr(request, 'query_params'):
            params = request.query_params.dict()

        # Include view-specific args if needed
        view_specific = self._get_view_specific_cache_components(request, *args, **kwargs)

        # Stable key components
        components = {
            'path': path,
            'version': version,
            'headers': '_'.join(f"{k}={v}" for k, v in sorted(headers.items())),
            'params': '_'.join(f"{k}={v}" for k, v in sorted(params.items())),
            'view_specific': view_specific
        }

        # Hash to avoid long keys
        key_str = '|'.join(f"{k}:{v}" for k, v in components.items() if v)
        return f"{self.CACHE_KEY_PREFIX}_{hashlib.md5(key_str.encode()).hexdigest()}"

    def _get_view_specific_cache_components(self, request, *args, **kwargs):
        """
        Hook for views to add their specific components to cache key
        """
        return ""

    def _invalidate_cache(self):
        """Invalidate all caches for this model by bumping version"""
        try:
            cache.incr(self.CACHE_VERSION_KEY)
        except ValueError:  # Key doesn't exist
            cache.set(self.CACHE_VERSION_KEY, 2, timeout=None)

    def cache_action(self, ttl=None):
        """
        Decorator to cache custom actions like approve, reject, etc.
        """
        def decorator(func):
            @wraps(func)
            def wrapper(viewset, request, *args, **kwargs):
                if not getattr(viewset, 'CACHE_ENABLED', True):
                    return func(viewset, request, *args, **kwargs)

                cache_key = viewset._generate_cache_key(request, *args, **kwargs)
                cached_data = cache.get(cache_key)

                if cached_data is not None:
                    return Response(cached_data, status=status.HTTP_200_OK)

                response = func(viewset, request, *args, **kwargs)

                if isinstance(response, Response) and response.status_code == status.HTTP_200_OK:
                    cache.set(cache_key, response.data, ttl or viewset.CACHE_TTL)

                return response
            return wrapper
        return decorator

    def cache_response(self, ttl=None):
        """
        Decorator to wrap DRF list/retrieve methods
        """
        def decorator(func):
            @wraps(func)
            def wrapper(viewset, request, *args, **kwargs):
                if not getattr(viewset, 'CACHE_ENABLED', True):
                    return func(viewset, request, *args, **kwargs)

                cache_key = viewset._generate_cache_key(request, *args, **kwargs)
                cached_data = cache.get(cache_key)

                if cached_data is not None:
                    return Response(cached_data, status=status.HTTP_200_OK)

                response = func(viewset, request, *args, **kwargs)

                if isinstance(response, Response) and response.status_code == status.HTTP_200_OK:
                    cache.set(cache_key, response.data, ttl or viewset.CACHE_TTL)

                return response
            return wrapper
        return decorator

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._invalidate_cache()

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._invalidate_cache()

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        self._invalidate_cache()

    @cache_response()
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @cache_response()
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
class BaseCachePermissionViewset(CachingMixin,PermissionRequiredMixin,viewsets.ModelViewSet):
    pass