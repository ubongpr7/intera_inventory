from rest_framework import permissions
import logging

logger = logging.getLogger(__name__)

from rest_framework_simplejwt.tokens import UntypedToken

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
    