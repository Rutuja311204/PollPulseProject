"""
Simple fixed-window rate limiting using Django's cache.

    allowed = hit("login:203.0.113.5", limit=10, window=300)

counts how many times a key was used in the current window (e.g. 5 minutes)
and returns False once the limit is exceeded. It protects login,
registration, public submissions and AI calls from brute force and spam.

Note: the default LocMemCache is per process. With several server processes
use a shared cache (Redis/Memcached) so the counts are shared.
The REST API additionally uses DRF's built-in throttle classes.
"""
from django.core.cache import cache

from .exceptions import RateLimited
from .utils import client_ip


def hit(key, limit, window):
    cache_key = f"rl:{key}"
    cache.add(cache_key, 0, timeout=window)  # creates the counter only if missing
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.set(cache_key, 1, timeout=window)
        count = 1
    return count <= limit


def check(request, scope, limit, window, extra=""):
    """Raise RateLimited when this client exceeded `limit` calls of `scope`."""
    key = f"{scope}:{client_ip(request)}:{extra}"
    if not hit(key, limit, window):
        raise RateLimited()
