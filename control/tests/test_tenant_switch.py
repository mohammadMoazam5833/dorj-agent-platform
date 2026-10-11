"""Account switch on one PC: a request key of another tenant than the page cookie is refused (2026-10-11)."""
from types import SimpleNamespace

from nova_control import auth, main, tenants

USERS = {"tok-a": {"name": "a", "email": "a@x"}, "tok-b": {"name": "b", "email": "b@x"}}


def req(headers=None, query=None, cookie=None):
    return SimpleNamespace(headers={k.lower(): v for k, v in (headers or {}).items()}, query_params=query or {},
                           cookies={auth.COOKIE: cookie} if cookie else {})


def test_stale_key_of_previous_account_is_rejected(monkeypatch):
    monkeypatch.setattr(tenants, "by_token", USERS.get)
    assert auth.token_from(req({"X-Session-API-Key": "tok-a"}, cookie="tok-b")) == ""
    assert auth.token_from(req(query={"session_api_key": "tok-a"}, cookie="tok-b")) == ""


def test_matching_key_fresh_signin_and_cli_still_work(monkeypatch):
    monkeypatch.setattr(tenants, "by_token", USERS.get)
    assert auth.token_from(req({"X-Session-API-Key": "tok-b"}, cookie="tok-b")) == "tok-b"
    assert auth.token_from(req(query={"t": "tok-b"}, cookie="tok-a")) == "tok-b"           # ?t= = new sign-in
    assert auth.token_from(req({"Authorization": "Bearer tok-a"})) == "tok-a"              # CLI, no cookie
    assert auth.token_from(req({"X-Session-API-Key": "tok-a"}, cookie="revoked")) == "tok-a"  # old cookie not valid


def test_boot_pins_backend_key_and_clears_previous_account_state():
    html = main._boot("tok-b", "llm", "b@x")
    assert "dorj-tenant" in html and '"tok-b"' in html and "openhands-backends" in html
    assert "openhands-last-conversation" not in html or "removeItem" in html
