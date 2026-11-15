"""Disk cache for every external call (Vexcel, Matterport, LLM). Ported from SiteWalk.

Nothing paid for is fetched twice, and the demo can run with the network off
(PROINSPECT_OFFLINE=1 makes a cache miss raise instead of calling out).
JSON-able results are stored as .json, bytes as .bin.
"""
import contextlib
import functools
import hashlib
import json
import os
import threading

from proinspect.config import CACHE_DIR

OFFLINE = os.environ.get("PROINSPECT_OFFLINE") == "1"
_LOCAL = threading.local()


class CacheMiss(RuntimeError):
    pass


@contextlib.contextmanager
def cache_only():
    prev = getattr(_LOCAL, "only", False)
    _LOCAL.only = True
    try:
        yield
    finally:
        _LOCAL.only = prev


def _key(namespace, name, args, kwargs):
    blob = json.dumps([name, args, kwargs], sort_keys=True, default=str)
    return CACHE_DIR / namespace / hashlib.sha256(blob.encode()).hexdigest()[:24]


def get(namespace, name, args=(), kwargs=None):
    base = _key(namespace, name, list(args), kwargs or {})
    if base.with_suffix(".json").exists():
        return True, json.loads(base.with_suffix(".json").read_text())
    if base.with_suffix(".bin").exists():
        return True, base.with_suffix(".bin").read_bytes()
    return False, None


def put(namespace, name, value, args=(), kwargs=None):
    base = _key(namespace, name, list(args), kwargs or {})
    base.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (bytes, bytearray)):
        base.with_suffix(".bin").write_bytes(value)
    else:
        base.with_suffix(".json").write_text(json.dumps(value, default=str))
    return base


def cached(namespace):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            hit, value = get(namespace, fn.__name__, args, kwargs)
            if hit:
                return value
            if OFFLINE or getattr(_LOCAL, "only", False):
                raise CacheMiss(f"{namespace}.{fn.__name__}{args} not cached")
            value = fn(*args, **kwargs)
            put(namespace, fn.__name__, value, args, kwargs)
            return value
        return wrapper
    return deco
