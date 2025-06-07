from rest_framework import permissions
from django.conf import settings
from ..services.permission_service import PermissionService
from ..services.user_service import UserService
import logging

logger = logging.getLogger(__name__)

class HasModelRequestPermission(permissions.BasePermission):
    """
    Microservice-adapted permission class that checks permissions via user service
    """
    
    def has_permission(self, request, view):
        """Check if user has required permissions"""
        
        # Get current user from request
        current_user = UserService.get_current_user(request)
        if not current_user:
            return False
        
        user_id = current_user.get('id')
        profile_id = request.headers.get('X-Profile-ID')
        
        if not user_id:
            return False
        
        # Check if user is the profile owner (full access)
        if profile_id and PermissionService.check_user_is_owner(user_id, profile_id):
            return True
        
        # Get required permission from view
        permission = getattr(view, 'required_permission', None)
        
        if not permission:
            # No specific permission required
            return True
        
        # Handle action-based permissions
        if isinstance(permission, dict):
            action = getattr(view, 'action', None)
            if action:
                permission = permission.get(action)
                if not permission:
                    return True  # No permission required for this action
        
        # Get user permissions from microservice
        user_permissions = PermissionService.get_user_permissions(user_id, profile_id)
        
        # Check if user has the required permission
        has_permission = permission in user_permissions
        
        if not has_permission:
            logger.warning(
                f"Permission denied for user {user_id}: "
                f"Required '{permission}', has {list(user_permissions)}"
            )
        
        return has_permission
    
    def has_object_permission(self, request, view, obj):
        """Check object-level permissions"""
        
        # First check model-level permission
        if not self.has_permission(request, view):
            return False
        
        # Additional object-level checks can be added here
        # For example, checking if the object belongs to the user's profile
        if hasattr(obj, 'profile_id'):
            profile_id = request.headers.get('X-Profile-ID')
            if profile_id and str(obj.profile_id) != str(profile_id):
                return False
        
        return True

class IsOwnerOrHasPermission(permissions.BasePermission):
    """
    Permission class that allows access to owners or users with specific permissions
    """
    
    def has_permission(self, request, view):
        current_user = UserService.get_current_user(request)
        if not current_user:
            return False
        
        user_id = current_user.get('id')
        profile_id = request.headers.get('X-Profile-ID')
        
        # Check ownership first
        if profile_id and PermissionService.check_user_is_owner(user_id, profile_id):
            return True
        
        # Fall back to permission check
        return HasModelRequestPermission().has_permission(request, view)

class PermissionRequiredMixin:
    """
    Mixin to add permission checking to views
    """
    required_permission = None
    permission_classes = [permissions.IsAuthenticated, HasModelRequestPermission]
    
    def get_required_permission(self):
        """Get the required permission for the current action"""
        permission = self.required_permission
        
        if isinstance(permission, dict):
            action = getattr(self, 'action', None)
            if action:
                return permission.get(action)
        
        return permission
    
    def check_permissions(self, request):
        """Override to add custom permission logic"""
        super().check_permissions(request)
        
        # Additional custom checks can be added here
        current_user = UserService.get_current_user(request)
        if current_user:
            # Log permission check for audit
            logger.info(
                f"Permission check for user {current_user.get('id')} "
                f"on {self.__class__.__name__}.{getattr(self, 'action', 'unknown')}"
            )
