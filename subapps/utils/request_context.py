from contextvars import ContextVar
import hashlib
import json
import os
from urllib import error, request as urlrequest
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import FieldDoesNotExist
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import UntypedToken
import jwt


_frontend_origin_context: ContextVar[str] = ContextVar("intera_frontend_origin", default="")


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
    permissions = _expanded_permissions(claims)
    context = _get_authorization_context(request)
    permissions.update(_expanded_permissions(context))
    return permissions


def _expanded_permissions(claims):
    permissions = set(str(item) for item in (claims.get("permissions") or []) if str(item).strip())
    wildcard_permissions = claims.get("wildcard_permissions") or {}
    for wildcard in claims.get("wildcards") or []:
        permissions.update(str(item) for item in wildcard_permissions.get(wildcard) or [] if str(item).strip())
    return permissions


def _matches_permission(required, granted_permissions):
    return any(
        granted == required
        or (granted.endswith(".*") and str(required).startswith(granted[:-1]))
        for granted in granted_permissions
    )


def _permission_cache_version():
    version = cache.get("permission:v1:version")
    if version in (None, ""):
        version = 1
        cache.set("permission:v1:version", version, None)
    return str(version)


def _permission_cache_key(*, user_id, profile_id, platform, service_name, permission):
    raw = json.dumps(
        {
            "permission": str(permission or ""),
            "platform": str(platform or ""),
            "profile_id": str(profile_id or ""),
            "service": str(service_name or ""),
            "user_id": str(user_id or ""),
            "version": _permission_cache_version(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"permission:v1:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _permission_service_config():
    return (
        (
            os.getenv("USER_SERVICE_URL")
            or os.getenv("INTERA_USERS_SERVICE_URL")
            or os.getenv("AUTHORIZATION_SERVICE_URL")
            or ""
        ).rstrip("/"),
        (
            os.getenv("PERMISSION_EVALUATION_SERVICE_KEY")
            or os.getenv("INTERA_INTERNAL_SERVICE_KEY")
            or os.getenv("SUBSCRIPTION_SERVICE_KEY")
            or ""
        ),
        os.getenv("KAFKA_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "inventory",
    )


def has_request_permission(request, permission):
    permission = str(permission or "").strip()
    if not permission:
        return False
    if _matches_permission(permission, get_request_permissions(request)):
        return True

    claims = _get_token_payload(request)
    context = _get_authorization_context(request)
    user_id = get_request_user_id(request)
    profile_id = get_request_profile_id(request)
    platform = context.get("platform") or claims.get("platform") or "intera_ims"
    if not user_id or not profile_id:
        return False

    base_url, service_key, service_name = _permission_service_config()
    if not base_url or not service_key:
        return False

    cache_key = _permission_cache_key(
        user_id=user_id,
        profile_id=profile_id,
        platform=platform,
        service_name=service_name,
        permission=permission,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return bool(cached)

    payload = json.dumps(
        {
            "user_id": user_id,
            "profile_id": profile_id,
            "platform": platform,
            "service": service_name,
            "permissions": [permission],
        }
    ).encode("utf-8")
    req = urlrequest.Request(
        f"{base_url}/permission_api/internal/evaluate-permissions/",
        data=payload,
        headers={"Content-Type": "application/json", "X-Intera-Service-Key": service_key},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=float(os.getenv("PERMISSION_EVALUATION_TIMEOUT", "2.0"))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, error.HTTPError, error.URLError):
        return False

    allowed = bool((data.get("grants") or {}).get(permission))
    cache.set(cache_key, allowed, int(data.get("expires_in") or os.getenv("PERMISSION_EVALUATION_CACHE_TTL_SECONDS", "3600")))
    return allowed


def invalidate_permission_cache(*, user_id="", profile_id="", platform=""):
    try:
        cache.incr("permission:v1:version")
    except ValueError:
        cache.set("permission:v1:version", 2, None)


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


def normalize_frontend_origin(value):
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", ""))


def allowed_frontend_origins():
    configured = getattr(settings, "FRONTEND_ACTION_ALLOWED_ORIGINS", None)
    if configured is None:
        configured = getattr(settings, "CORS_ALLOWED_ORIGINS", [])
    origins = [
        *list(configured or []),
        getattr(settings, "FRONTEND_SITE_URL", ""),
        getattr(settings, "SITE_URL", ""),
    ]
    return {origin for origin in (normalize_frontend_origin(item) for item in origins) if origin}


def frontend_origin_from_request(request, *, default=None):
    candidates = [
        request.headers.get("X-Intera-Frontend-Origin"),
        request.headers.get("X-Frontend-Origin"),
        request.headers.get("Origin"),
        request.headers.get("Referer"),
        default,
        getattr(settings, "FRONTEND_SITE_URL", ""),
        getattr(settings, "SITE_URL", ""),
    ]
    allowed = allowed_frontend_origins()
    for candidate in candidates:
        origin = normalize_frontend_origin(candidate)
        if origin and (not allowed or origin in allowed):
            return origin
    return ""


def set_frontend_origin_context(origin):
    return _frontend_origin_context.set(normalize_frontend_origin(origin))


def reset_frontend_origin_context(token):
    _frontend_origin_context.reset(token)


def current_frontend_origin():
    return _frontend_origin_context.get() or normalize_frontend_origin(
        getattr(settings, "FRONTEND_SITE_URL", "")
    )


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
