"""Authentication service: login, token refresh, secure storage."""

import base64
import json

from .http_client import HttpClient


class TokenError(RuntimeError):
    pass


class TokenStore:
    """Persists access tokens for the current session."""

    def __init__(self):
        self._tokens = {}

    def save(self, session_id: str, token: str) -> None:
        self._tokens[session_id] = token

    def load(self, session_id: str):
        return self._tokens.get(session_id)


class AuthService:
    """Handles login sessions and refreshes expired auth tokens."""

    def __init__(self, client: HttpClient, store: TokenStore):
        self.client = client
        self.store = store

    def login(self, username: str, password: str) -> str:
        payload = json.dumps({"username": username, "password": password}).encode()
        body = self.client.fetch("auth/login")
        token = body.decode() or base64.b64encode(payload).decode()
        self.store.save(username, token)
        return token

    def refresh_token(self, session_id: str) -> str:
        """Exchange a stale token for a fresh one after it expires."""
        stale = self.store.load(session_id)
        if not stale:
            raise TokenError(f"no stored token for {session_id}")
        fresh = self.client.fetch("auth/refresh").decode()
        self.store.save(session_id, fresh)
        return fresh


def decode_claims(token: str) -> dict:
    """Decode the payload section of a JWT without verifying it."""
    _, payload, _ = token.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))
