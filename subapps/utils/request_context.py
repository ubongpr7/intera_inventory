from django.core.exceptions import FieldDoesNotExist
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import UntypedToken
import jwt


def _get_authorization_context(request):
    token = request.META.get("HTTP_X_INTERA_AUTHORIZATION_CONTEXT") or request.headers.get(
        "X-Intera-Authorization-Context"
    )
    if not token:
        return {}
    try:
        context = jwt.decode(
            token,
            getattr(settings, "JWT_VERIFYING_KEY", None),
            algorithms=[getattr(settings, "JWT_ALGORITHM", "RS256")],
            options={"verify_aud": False, "verify_iss": False},
        )
        access = _get_token_payload(request)
        if context.get("token_type") != "intera_authorization_context" or str(context.get("user_id") or "") not in {
            str(access.get("user_id") or getattr(request.user, "id", "")),
            str(getattr(request.user, "id", "")),
        }:
            return {}
        if str(context.get("profile_id") or "") != str(access.get("profile_id") or ""):
            return {}
        return context
    except Exception:
        return {}


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
    claims = _get_token_payload(request)
    permissions = set(claims.get("permissions") or [])
    context = _get_authorization_context(request)
    permissions.update(context.get("permissions") or [])
    wildcard_permissions = context.get("wildcard_permissions") or {}
    for wildcard in context.get("wildcards") or []:
        permissions.update(wildcard_permissions.get(wildcard) or [])
    return permissions


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


def _lookup_path_exists(model, lookup_path):
    if model is None or not lookup_path:
        return False

    current_model = model
    for index, part in enumerate(lookup_path.split("__")):
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            return False

        if index == len(lookup_path.split("__")) - 1:
            return True

        current_model = getattr(getattr(field, "remote_field", None), "model", None)
        if current_model is None:
            return False

    return True


def build_identity_lookup(*, canonical_field, legacy_field=None, value=None, model=None):
    lookup = Q()
    normalized_value = coerce_identity_id(value)
    legacy_value = None if value in (None, "") else str(value).strip()

    if normalized_value is not None and _lookup_path_exists(model, canonical_field):
        lookup |= Q(**{canonical_field: normalized_value})
        legacy_value = str(normalized_value)

    if legacy_field and legacy_value not in (None, "") and _lookup_path_exists(model, legacy_field):
        lookup |= Q(**{legacy_field: legacy_value})

    return lookup


def scope_queryset_by_identity(queryset, *, canonical_field, legacy_field=None, value=None):
    lookup = build_identity_lookup(
        canonical_field=canonical_field,
        legacy_field=legacy_field,
        value=value,
        model=queryset.model,
    )
    if not lookup.children:
        return queryset
    return queryset.filter(lookup)
