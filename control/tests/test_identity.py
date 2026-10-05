import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nova_control import auth, tenants
from nova_control.identity import slug_for

ALICE = {"name": "alice", "email": "alice@isigpu.local", "token": "a" * 48, "groups": ["nova-admins"], "disabled": False}
BOB = {"name": "bob", "email": "bob@isigpu.local", "token": "b" * 48, "groups": [], "disabled": True}


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(tenants, "all_customers", lambda fresh=False: [dict(ALICE), dict(BOB)])


def req(headers=None, query=b"", cookie=None):
    h = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookie:
        h.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": h, "query_string": query})


def test_slug_is_dns_safe_and_stable():
    assert slug_for("Ali.Rezaei@isigpu.local") == "ali-rezaei"
    assert slug_for("a_b+c@x.org") == "a-b-c"
    assert slug_for("x" * 80 + "@y.z") == "x" * 40


@pytest.mark.parametrize("r", [
    req({"X-Session-API-Key": "a" * 48}),
    req({"Authorization": "Bearer " + "a" * 48}),
    req(query=b"session_api_key=" + b"a" * 48),
    req(query=b"t=" + b"a" * 48),
    req(cookie="nova_t=" + "a" * 48),
])
def test_tenant_token_identifies_user_wherever_the_client_sends_it(r):
    ident = auth.current_identity(r)
    assert ident.email == "alice@isigpu.local" and ident.slug == "alice" and auth.is_admin(ident)


@pytest.mark.parametrize("r", [
    req(),
    req({"X-Session-API-Key": "nope"}),
    req(cookie="ns_t=alice"),                                  # the VM ingress' cookie-by-name: not a credential
    req({"kubeflow-userid": "alice@isigpu.local", "x-forwarded-email": "alice@isigpu.local"}),
    req({"X-Session-API-Key": "b" * 48}),                      # disabled account
])
def test_no_valid_token_is_401(r):
    with pytest.raises(HTTPException) as e:
        auth.current_identity(r)
    assert e.value.status_code == 401


def test_gateway_view_has_only_active_customers_and_no_emails():
    view = tenants.gateway_view()
    assert view == {"customers": [{"name": "alice", "token": "a" * 48, "roots": []}]}
