"""Serialize native PDF operations across preview and composition worker threads."""
from functools import wraps
from threading import RLock

_LOCK = RLock()

def serialized_pdf(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _LOCK:
            return function(*args, **kwargs)
    return wrapped
