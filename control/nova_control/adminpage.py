"""Dorj Agent admin panel page, adapted from nova-admin's PAGE template.

The Kubernetes control plane keeps the original JSON API shapes, but signs in with an
organisation account (realm ``nova``, group ``nova-admins``) instead of a shared password.
Each visible edit asserts an anchor, so upstream UI changes fail loudly instead of silently
reintroducing old branding.
"""
from __future__ import annotations

import base64
import functools
import re
from pathlib import Path

from .config import settings

EDITS = [
    ('<input id="pw" type="password" placeholder="رمز مدیریت" autofocus onkeydown="if(event.key===\\\'Enter\\\')doLogin()">',
     '<input id="un" type="text" placeholder="نام کاربری سازمانی" autofocus dir="ltr" onkeydown="if(event.key===\\\'Enter\\\')document.getElementById(\\\'pw\\\').focus()">'
     '<div style="height:8px"></div><input id="pw" type="password" placeholder="گذرواژه" dir="ltr" onkeydown="if(event.key===\\\'Enter\\\')doLogin()">'),
    ('post("/api/login",{password:pw})', 'post("/api/login",{username:document.getElementById("un").value.trim(),password:pw})'),
    ('renderLogin(r.j.error||"رمز اشتباه است")', 'renderLogin(r.j.error||"نام کاربری یا گذرواژه نادرست است")'),
    ('\'<button class="ghost mini" onclick="changePw()">تغییر رمز</button>\'+', ''),
    ("(t.hasPortalUser?'تغییر رمز':'رمز')", "'خروج دستگاه‌ها'"),
    ('''function tPw(n){
  var p=prompt("رمز پورتال برای "+n+" (حداقل ۴ کاراکتر):");
  if(p==null)return;
  if(p.length<4){toast("رمز کوتاه است");return}
  post("/api/tenants/"+n+"/password",{password:p}).then(function(r){
    toast(r.j.ok?"رمز "+r.j.username+" ثبت شد ✓":("خطا: "+(r.j.error||r.s)));loadTenants();
  });
}''', '''function tPw(n){
  if(!confirm("همه‌ی دستگاه‌های "+n+" از حساب خارج شوند؟ (توکن کاربر عوض می‌شود و باید دوباره وارد شود)"))return;
  post("/api/tenants/"+n+"/rotate",{}).then(function(r){
    toast(r.j.ok?"دستگاه‌های "+r.j.username+" خارج شدند ✓":("خطا: "+(r.j.error||r.s)));loadTenants();
  });
}'''),
    ('placeholder="نام کاربری مثلاً node3"', 'placeholder="نام کاربری Active Directory (مثلاً ali)"'),
    ('<span class="mut">رمز، همان لاگین پورتال NovaAgent است</span>', '<span class="mut">کاربران با حساب سازمانی (Active Directory) وارد اپ می‌شوند</span>'),
    ('<th>پورت</th>', '<th>Workspace</th>'),
    ('<title>NovaAgent | پنل مدیریت</title>', '<title>Dorj Agent | پنل مدیریت</title>'),
    (':root{--bg:#0d0d0f;--card:#161619;--card2:#1d1d21;--line:#2a2a30;--tx:#ececf1;--tx2:#9a9aa5;--acc:#d97757;--acc2:#8b7cf6;--ok:#3fb950;--bad:#f85149;--warn:#d29922}',
     ':root{--bg:#0B1B3A;--card:#11244e;--card2:#183567;--line:#2b4a8f;--tx:#f5f7fb;--tx2:#b7c3db;--acc:#E8A317;--acc2:#F4C24B;--ok:#3fb950;--bad:#f85149;--warn:#d29922}\n'
     'body{background:linear-gradient(120deg,#1E3A8A,#16295e)}\n'
     '.lcard{border-color:rgba(232,163,23,.25);box-shadow:0 24px 70px rgba(4,12,31,.55)}\n'
     '.lcard h1{display:flex;align-items:center;justify-content:center;gap:10px}\n'
     '.lcard h1 img{width:38px;height:38px;border-radius:10px}\n'
     '.lcard h1 span{background:linear-gradient(90deg,var(--acc),var(--acc2))}'),
    ('\'<h1><span>NovaAgent</span></h1><p>پنل مدیریت و مانیتورینگ سرور</p>\'+',
     '\'<h1><img src="__DORJ_LOGO__" alt=""><span>دستیار دُرج</span></h1><p>پنل مدیریت و مانیتورینگ سرور</p>\'+'),
    ('background:rgba(13,13,15,.85)', 'background:rgba(11,27,58,.88)'),
    ('\'<div class="top"><div class="brand"><span class="logo"><svg width="16" height="16" viewBox="0 0 64 64"><path d="M19 46V18l26 28V18" stroke="#d97757" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg></span><span>NovaAgent</span>&nbsp;· پنل مدیریت</div>\'+',
     '\'<div class="top"><div class="brand"><img src="__DORJ_LOGO__" alt="" style="width:28px;height:28px;border-radius:8px"><span>Dorj Agent</span><span class="mut">· پنل مدیریت</span></div>\'+'),
    ('pre{background:#0a0a0c;', 'pre{background:#081431;'),
    ('spark(r1,S.hist,"#d97757")', 'spark(r1,S.hist,"#E8A317")'),
    ('spark(r2,S.histLoad,"#8b7cf6")', 'spark(r2,S.histLoad,"#4C6EF5")'),
]


def _logo_uri() -> str:
    # The web bundle in the same container carries the exact platform favicon/logo set.
    logo_file = Path(settings.web_root) / "favicon-64x64.png"
    if logo_file.is_file():
        return "data:image/png;base64," + base64.b64encode(logo_file.read_bytes()).decode("ascii")
    return (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
        "%3Crect width='64' height='64' rx='14' fill='%230B1B3A'/%3E"
        "%3Cpath d='M23 47V17h11c9 0 16 6 16 15s-7 15-16 15z' fill='%23fff'/%3E%3C/svg%3E"
    )


@functools.lru_cache(maxsize=1)
def render() -> str:
    src = open(settings.admin_page, encoding="utf-8").read()
    a = src.index("const PAGE = `") + len("const PAGE = `")
    page = src[a:src.index("`;\n", a)]
    # evaluate the JS template literal's escapes (the PAGE has no ${} interpolations)
    if "${" in page:
        raise RuntimeError("nova-admin PAGE gained template interpolations; update adminpage.py")
    page = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), page, flags=re.S)
    logo = _logo_uri()
    page = page.replace("__FAVICON__", logo)
    for old, new in EDITS:
        if old not in page:
            raise RuntimeError(f"nova-admin page anchor not found: {old[:70]!r}")
        page = page.replace(old, new, 1)
    page = page.replace("__DORJ_LOGO__", logo)
    # the new-user form has no password field any more
    page = re.sub(r'<input id="ntpw"[^>]*>', '<input id="ntpw" type="hidden" value="">', page, count=1)
    if "NovaAgent" in page or "OpenHands" in page:
        raise RuntimeError("old product name remains in the rendered admin page")
    return page
