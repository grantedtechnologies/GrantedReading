"""Redis-backed ephemeral state for multi-worker deploys.

redis_client — shared connection
sessions     — login cookies + pending checkout
worksheets   — daily quota, pending PDFs, save locks

Import through cache.py (the facade), same idea as llm_pipeline → reading_level.
"""

from . import sessions, worksheets
from .redis_client import get_redis, redis_url, reset_client

__all__ = [
    "get_redis",
    "redis_url",
    "reset_client",
    "sessions",
    "worksheets",
]
