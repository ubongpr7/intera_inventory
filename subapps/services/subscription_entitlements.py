import json
import logging
import os
import hashlib
from urllib import request

from django.core.cache import cache
from rest_framework.exceptions import PermissionDenied

logger = logging.getLogger(__name__)


def _decision_cache_version_key(*, profile_id, application):
    return f'subscription-decision:v1:version:{application}:{profile_id}'


def _decision_cache_version(*, profile_id, application):
    version_key = _decision_cache_version_key(profile_id=profile_id, application=application)
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, None)
    return version


def _decision_cache_key(*, profile_id, application, feature, usage, requested):
    raw = json.dumps(
        {
            'application': str(application),
            'feature': str(feature),
            'profile_id': str(profile_id),
            'requested': int(requested),
            'usage': int(usage),
            'version': _decision_cache_version(profile_id=profile_id, application=application),
        },
        sort_keys=True,
        separators=(',', ':'),
    )
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return f'subscription-decision:v1:{digest}'


def invalidate_subscription_decisions(*, profile_id, application=None):
    """Invalidate cached entitlement decisions for one tenant/application."""
    application = str(application or os.getenv('SUBSCRIPTION_APPLICATION_SLUG', 'intera-ims')).strip()
    version_key = _decision_cache_version_key(profile_id=profile_id, application=application)
    try:
        cache.incr(version_key)
    except ValueError:
        cache.set(version_key, 2, None)


def enforce_subscription_limit(*, profile_id, feature, usage, requested=1, application=None):
    """Ask the Subscription service for a tenant-scoped entitlement decision."""
    mode = os.getenv('SUBSCRIPTION_ENFORCEMENT_MODE', 'off').lower()
    if mode == 'off':
        return None
    if profile_id in (None, ''):
        raise PermissionDenied({'code': 'profile_required', 'detail': 'An active workspace is required.'})

    application = str(application or os.getenv('SUBSCRIPTION_APPLICATION_SLUG', 'intera-ims')).strip()
    cache_key = _decision_cache_key(
        profile_id=profile_id,
        application=application,
        feature=feature,
        usage=usage,
        requested=requested,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        if not cached.get('allowed') and mode == 'enforce':
            raise PermissionDenied({
                'code': 'subscription_limit_reached',
                'detail': 'Your current plan limit has been reached.',
                **cached,
            })
        return cached

    base_url = os.getenv('SUBSCRIPTION_SERVICE_URL', 'http://subscriptions:8550').rstrip('/')
    service_key = os.getenv('SUBSCRIPTION_SERVICE_KEY', '')
    payload = json.dumps({
        'profile_id': str(profile_id),
        'application': application,
        'feature': feature,
        'usage': usage,
        'requested': requested,
    }).encode()
    req = request.Request(
        f'{base_url}/internal/v1/entitlements/',
        data=payload,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'X-Intera-Service-Key': service_key,
        },
    )
    try:
        with request.urlopen(req, timeout=float(os.getenv('SUBSCRIPTION_SERVICE_TIMEOUT', '2.0'))) as response:
            decision = json.loads(response.read().decode())
    except (OSError, TimeoutError, ValueError) as exc:
        logger.warning('Subscription check unavailable feature=%s profile=%s: %s', feature, profile_id, exc)
        if mode == 'enforce':
            raise PermissionDenied({'code': 'subscription_service_unavailable', 'detail': 'Subscription limits could not be verified. Please retry.'})
        return None
    if isinstance(decision, dict):
        cache.set(
            cache_key,
            decision,
            max(int(os.getenv('SUBSCRIPTION_DECISION_CACHE_TTL_SECONDS', '30')), 1),
        )
    if not decision.get('allowed'):
        logger.info('Subscription limit reached feature=%s profile=%s usage=%s limit=%s', feature, profile_id, usage, decision.get('limit'))
        if mode == 'enforce':
            raise PermissionDenied({'code': 'subscription_limit_reached', 'detail': 'Your current plan limit has been reached.', 'feature': feature, 'limit': decision.get('limit'), 'usage': usage, 'upgrade_required': True})
    return decision
