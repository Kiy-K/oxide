"""Tests for auth token refresh flow."""

from oxidepy.auth import AuthService, TokenStore, decode_claims
from oxidepy.http_client import HttpClient


class FakeClient(HttpClient):
    def __init__(self, body=b"fresh-token"):
        super().__init__("http://fake")
        self.body = body

    def fetch(self, path):
        return self.body


def test_refresh_token_stores_new_token():
    store = TokenStore()
    service = AuthService(FakeClient(), store)
    store.save("khoi", "stale-token")
    fresh = service.refresh_token("khoi")
    assert fresh == "fresh-token"
    assert store.load("khoi") == "fresh-token"


def test_decode_claims_parses_payload():
    token = "header.eyJzdWIiOiAia2hvaSJ9.signature"
    assert decode_claims(token)["sub"] == "khoi"
