"""
api/security.py — Shared-secret authentication for the Triage API.

The API is not exposed to browsers directly. The Next.js frontend (a trusted
server) calls it server-to-server with a secret in the `X-API-Key` header, and
enforces real per-user auth (Supabase) at its own layer. This dependency simply
proves the caller is that trusted server.

Set the secret via the TRIAGE_API_SECRET environment variable on both sides.
"""

import hmac
import os

from fastapi import Header, HTTPException, status


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("TRIAGE_API_SECRET")

    # Fail closed: if the server has no secret configured, reject everything
    # rather than silently running an open API.
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TRIAGE_API_SECRET is not configured on the server.",
        )

    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )
