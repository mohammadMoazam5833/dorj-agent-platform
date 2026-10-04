"""OAuth 2.0 Device Authorization Grant (RFC 8628) against NovaAgent's own Keycloak realm, plus token refresh.
Tokens are stored in ~/.config/nova/credentials.json with mode 0600."""
import json
import os
import stat
import time
from pathlib import Path

import httpx

CONFIG_DIR = Path(os.environ.get("NOVA_CONFIG_DIR", Path.home() / ".config" / "nova"))
CRED_FILE = CONFIG_DIR / "credentials.json"
DEFAULT_SERVER = os.environ.get("NOVA_SERVER", "https://nova.isigpu.local")
DEFAULT_ISSUER = os.environ.get("NOVA_ISSUER", "https://identity.isigpu.local/realms/nova")
CLIENT_ID = os.environ.get("NOVA_CLIENT_ID", "nova-cli")
GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class AuthError(RuntimeError):
    pass


def _verify():
    # internal CA: honour NOVA_CA_BUNDLE / SSL_CERT_FILE, else system store
    return os.environ.get("NOVA_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True


def save(creds: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CRED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(creds))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(CRED_FILE)


def load() -> dict:
    try:
        return json.loads(CRED_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def logout() -> None:
    creds = load()
    # revoke the (offline) refresh token at the realm too, so a copied credentials file stops working
    if creds.get("refresh_token") and creds.get("issuer"):
        try:
            ep = _discovery(creds["issuer"]).get("revocation_endpoint")
            if ep:
                httpx.post(ep, data={"token": creds["refresh_token"], "token_type_hint": "refresh_token",
                                     "client_id": CLIENT_ID}, verify=_verify(), timeout=15)
        except (httpx.HTTPError, AuthError, ValueError):
            pass
    try:
        CRED_FILE.unlink()
    except FileNotFoundError:
        pass


def _discovery(issuer: str) -> dict:
    r = httpx.get(f"{issuer}/.well-known/openid-configuration", verify=_verify(), timeout=15)
    r.raise_for_status()
    return r.json()


def device_login(server: str = DEFAULT_SERVER, issuer: str = DEFAULT_ISSUER, out=print) -> dict:
    disc = _discovery(issuer)
    dev_ep = disc.get("device_authorization_endpoint") or f"{issuer}/device/code"
    r = httpx.post(dev_ep, data={"client_id": CLIENT_ID, "scope": "openid email profile offline_access"},
                   verify=_verify(), timeout=15)
    if r.status_code >= 300:
        raise AuthError(f"device authorization failed: {r.status_code} {r.text[:200]}")
    d = r.json()
    out(f"Baraye login in address ro dar browser baz konid:\n  {d.get('verification_uri_complete') or d['verification_uri']}\n"
        f"Code: {d['user_code']}")
    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 600))
    while time.time() < deadline:
        time.sleep(interval)
        t = httpx.post(disc["token_endpoint"], data={"grant_type": GRANT, "device_code": d["device_code"], "client_id": CLIENT_ID},
                       verify=_verify(), timeout=15)
        body = t.json() if t.headers.get("content-type", "").startswith("application/json") else {}
        if t.status_code == 200:
            creds = _store_tokens(body, server, issuer, disc["token_endpoint"])
            return creds
        err = body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise AuthError(f"login failed: {err or t.status_code}")
    raise AuthError("login timed out")


def _store_tokens(body: dict, server: str, issuer: str, token_endpoint: str) -> dict:
    creds = {
        "server": server, "issuer": issuer, "token_endpoint": token_endpoint,
        "id_token": body["id_token"], "refresh_token": body.get("refresh_token"),
        "expires_at": time.time() + int(body.get("expires_in", 3600)) - 60,
    }
    save(creds)
    return creds


def bearer() -> tuple:
    """(server, id_token), refreshing the token when it is about to expire."""
    creds = load()
    if not creds:
        raise AuthError("login nakardid: `nova login`")
    if time.time() >= creds.get("expires_at", 0):
        if not creds.get("refresh_token"):
            raise AuthError("session tamoom shode: `nova login`")
        t = httpx.post(creds["token_endpoint"], data={"grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
                                                       "client_id": CLIENT_ID}, verify=_verify(), timeout=15)
        if t.status_code != 200:
            raise AuthError("session tamoom shode: `nova login`")
        body = t.json()
        body.setdefault("refresh_token", creds["refresh_token"])
        creds = _store_tokens(body, creds["server"], creds["issuer"], creds["token_endpoint"])
    return creds["server"], creds["id_token"]
