
from subapps.services.identity_directory import IdentityDirectory

class UserDetailMixin:
    """Mixin to add user details to serializers"""
    
    def get_user_details(self, user_id):
        """Get user details from the local identity projection."""
        if not user_id:
            return None
        return IdentityDirectory.get_minimal_user_data(user_id)

    def resolve_user_reference(self, obj, canonical_field, legacy_field):
        canonical_value = getattr(obj, canonical_field, None)
        if canonical_value not in (None, ""):
            return canonical_value
        return getattr(obj, legacy_field, None)
