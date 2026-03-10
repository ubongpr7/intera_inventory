from django.core.cache import cache
from typing import Optional, Dict, Any

from mainapps.identity.models import IdentityUser
from subapps.utils.request_context import (
    get_request_auth_headers,
    get_request_company_code,
    get_request_email,
    get_request_full_name,
    get_request_membership_role,
    get_request_owner_id,
    get_request_permissions,
    get_request_profile_id,
    get_request_user_id,
)


class IdentityDirectory:
    """Access local identity projections and token-derived request context."""

    CACHE_TIMEOUT = 300

    @classmethod
    def get_user_details(cls, user_id: str) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None

        cache_key = f"user_details_{user_id}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data

        try:
            identity_user = IdentityUser.objects.filter(user_id=int(user_id)).first()
        except (TypeError, ValueError):
            return None

        if not identity_user:
            return None

        user_data = {
            "id": identity_user.user_id,
            "email": identity_user.email,
            "full_name": identity_user.full_name,
            "first_name": identity_user.full_name.split(" ", 1)[0] if identity_user.full_name else "",
            "last_name": identity_user.full_name.split(" ", 1)[1] if identity_user.full_name and " " in identity_user.full_name else "",
            "profile_image": None,
            "is_active": identity_user.is_active,
        }
        cache.set(cache_key, user_data, cls.CACHE_TIMEOUT)
        return user_data

    @classmethod
    def get_current_user(cls, request) -> Optional[Dict[str, Any]]:
        user_id = get_request_user_id(request, as_str=False)
        if not user_id:
            return None

        return {
            "id": user_id,
            "email": get_request_email(request),
            "full_name": get_request_full_name(request),
            "profile_id": get_request_profile_id(request, as_str=False),
            "company_code": get_request_company_code(request),
            "membership_role": get_request_membership_role(request),
            "owner_id": get_request_owner_id(request, as_str=False),
            "permissions": sorted(get_request_permissions(request)),
        }

    @classmethod
    def get_auth_header(cls, request) -> Optional[str]:
        return get_request_auth_headers(request)

    @classmethod
    def get_minimal_user_data(cls, user_id: str) -> Dict[str, Any]:
        user_data = cls.get_user_details(user_id)
        if user_data:
            return {
                "id": user_data.get("id"),
                "email": user_data.get("email"),
                "first_name": user_data.get("first_name", ""),
                "last_name": user_data.get("last_name", ""),
                "full_name": f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
                "profile_image": user_data.get("profile_image"),
            }
        return {
            "id": user_id,
            "email": "Unknown",
            "first_name": "",
            "last_name": "",
            "full_name": "Unknown User",
            "profile_image": None,
        }
