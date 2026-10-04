import pytest
from fastapi import HTTPException

from nova_control.identity import identity_from_headers, slug_for


def test_slug_is_dns_safe_and_stable():
    assert slug_for("Ali.Rezaei@isigpu.local") == "ali-rezaei"
    assert slug_for("a_b+c@x.org") == "a-b-c"
    assert slug_for("x" * 80 + "@y.z") == "x" * 40


def test_identity_comes_only_from_auth_proxy_headers():
    ident = identity_from_headers({"x-auth-request-email": "Bob@isigpu.local", "x-auth-request-groups": "a,b"})
    assert ident.email == "bob@isigpu.local" and ident.slug == "bob" and ident.groups == ("a", "b")


@pytest.mark.parametrize("headers", [{}, {"x-auth-request-email": "not-an-email"}, {"x-session-api-key": "x"}])
def test_missing_identity_is_401(headers):
    with pytest.raises(HTTPException) as e:
        identity_from_headers(headers)
    assert e.value.status_code == 401
