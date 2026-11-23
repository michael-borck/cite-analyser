"""Typed lookup failures — port of lookupError.ts.

A failed database lookup is not the same as "this reference does not exist".
When a service is rate-limited, times out, or is down, the verifier must say
so rather than reporting a confident "not found". LookupError carries which
service failed and why, so the reason can travel to the verdict."""

from __future__ import annotations

import httpx

LookupFailureReason = str  # 'rate_limited' | 'timeout' | 'server_error' | 'network' | 'unknown'


class LookupError(Exception):
    def __init__(self, service: str, reason: LookupFailureReason, message: str | None = None):
        self.service = service
        self.reason = reason
        super().__init__(message or f"{service} lookup failed ({reason})")


def reason_from_status(status: int) -> LookupFailureReason:
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "unknown"


def reason_from_exception(err: Exception) -> LookupFailureReason:
    if isinstance(err, httpx.TimeoutException):
        return "timeout"
    return "network"
