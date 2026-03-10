from django.db.models import Q
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import UntypedToken


def _get_token_payload(request):
    auth = getattr(request, "auth", None)
    if auth is not None:
        payload = getattr(auth, "payload", None)
        if payload is not None:
            return payload
        if isinstance(auth, dict):
            return auth
        if hasattr(auth, "get"):
            return auth

    auth_header = request.META.get("HTTP_AUTHORIZATION") or request.headers.get("Authorization")
    if not auth_header:
        return {}

    parts = auth_header.split()
    if len(parts) != 2:
        return {}

    try:
        return UntypedToken(parts[1]).payload
    except Exception:
        return {}


def get_request_claim(request, claim_name, default=None):
    return _get_token_payload(request).get(claim_name, default)


def get_request_profile_id(request, *, required=False, as_str=True):
    profile_id = get_request_claim(request, "profile_id")
    if profile_id in (None, ""):
        if required:
            raise AuthenticationFailed("Access token missing profile_id claim.")
        return None
    return str(profile_id) if as_str else profile_id


def get_request_user_id(request, *, required=False, as_str=True):
    user_id = getattr(getattr(request, "user", None), "id", None)
    if user_id in (None, ""):
        user_id = get_request_claim(request, "user_id")
    if user_id in (None, ""):
        user_id = get_request_claim(request, "id")
    if user_id in (None, ""):
        if required:
            raise AuthenticationFailed("Access token missing user identifier.")
        return None
    return str(user_id) if as_str else user_id


def get_request_permissions(request):
    permissions = get_request_claim(request, "permissions", [])
    if not permissions:
        return set()
    return set(permissions)


def get_request_owner_id(request, *, as_str=True):
    owner_id = get_request_claim(request, "owner_id")
    if owner_id in (None, ""):
        return None
    return str(owner_id) if as_str else owner_id


def get_request_company_code(request):
    return get_request_claim(request, "company_code")


def get_request_membership_role(request):
    return get_request_claim(request, "membership_role")


def get_request_email(request):
    return get_request_claim(request, "email")


def get_request_full_name(request):
    return get_request_claim(request, "full_name") or get_request_claim(request, "name")


def get_request_auth_headers(request):
    auth_header = request.META.get("HTTP_AUTHORIZATION") or request.headers.get("Authorization")
    if not auth_header:
        return {}
    return {"Authorization": auth_header}


def get_identity_cache_key(request, default="default"):
    profile_id = get_request_profile_id(request)
    if profile_id in (None, ""):
        return default
    return str(profile_id)


def coerce_identity_id(value):
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def build_identity_lookup(*, canonical_field, legacy_field=None, value=None):
    lookup = Q()
    normalized_value = coerce_identity_id(value)
    legacy_value = None if value in (None, "") else str(value).strip()

    if normalized_value is not None:
        lookup |= Q(**{canonical_field: normalized_value})
        legacy_value = str(normalized_value)

    if legacy_field and legacy_value not in (None, ""):
        lookup |= Q(**{legacy_field: legacy_value})

    return lookup


def scope_queryset_by_identity(queryset, *, canonical_field, legacy_field=None, value=None):
    lookup = build_identity_lookup(
        canonical_field=canonical_field,
        legacy_field=legacy_field,
        value=value,
    )
    if not lookup.children:
        return queryset
    return queryset.filter(lookup)
