import time
from threading import Lock

_cache = {}
_lock = Lock()

CACHE_TTL = {
    'vehicles': 600,
    'driver_info': 600,
    'active_ride': 30,
}

def _get_cache_key(user_id, data_type):
    return f"{user_id}:{data_type}"

def get_cached(user_id, data_type):
    key = _get_cache_key(user_id, data_type)
    with _lock:
        if key in _cache:
            entry = _cache[key]
            ttl = CACHE_TTL.get(data_type, 60)
            if time.time() - entry['timestamp'] < ttl:
                return entry['data']
            del _cache[key]
    return None

def set_cached(user_id, data_type, data):
    key = _get_cache_key(user_id, data_type)
    with _lock:
        _cache[key] = {
            'data': data,
            'timestamp': time.time()
        }

def invalidate_cache(user_id, data_type=None):
    with _lock:
        if data_type:
            key = _get_cache_key(user_id, data_type)
            if key in _cache:
                del _cache[key]
        else:
            keys_to_delete = [k for k in _cache if k.startswith(f"{user_id}:")]
            for k in keys_to_delete:
                del _cache[k]

def get_vehicles(user_id, fetch_func):
    cached = get_cached(user_id, 'vehicles')
    if cached is not None:
        return cached
    data = fetch_func()
    set_cached(user_id, 'vehicles', data)
    return data

def get_driver_info(user_id, fetch_func):
    cached = get_cached(user_id, 'driver_info')
    if cached is not None:
        return cached
    data = fetch_func()
    set_cached(user_id, 'driver_info', data)
    return data

def get_active_ride(user_id, fetch_func):
    cached = get_cached(user_id, 'active_ride')
    if cached is not None:
        return cached
    data = fetch_func()
    set_cached(user_id, 'active_ride', data)
    return data
