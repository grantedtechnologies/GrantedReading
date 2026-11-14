"""Stateless Redis facade — sessions, quota, pending worksheets.

Per-service implementations live in the stateless/ package. This module is the
pipeline entry point (same role llm_pipeline plays for reading_level / Foundry).
"""

from __future__ import annotations

from stateless import sessions
from stateless.redis_client import get_redis, redis_url, reset_client
from stateless.worksheets import (
    acquire_save_lock,
    load_pending_worksheet,
    mark_worksheet_used,
    release_save_lock,
    stash_pending_worksheet,
    worksheet_quota_used,
)

__all__ = [
    "sessions",
    "get_redis",
    "redis_url",
    "reset_client",
    "worksheet_quota_used",
    "mark_worksheet_used",
    "stash_pending_worksheet",
    "load_pending_worksheet",
    "acquire_save_lock",
    "release_save_lock",
]
