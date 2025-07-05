
from subapps.services.microservices.user_service import UserService

class UserDetailMixin:
    """Mixin to add user details to serializers"""
    
    def get_user_details(self, user_id):
        """Get user details from user service"""
        if not user_id:
            return None
        return UserService.get_minimal_user_data(user_id)
