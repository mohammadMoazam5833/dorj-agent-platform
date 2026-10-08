"""Render Dorj Agent's own download page (Dorj Agent scripts/download-site/index.html) for this deployment
and gather the files it links: the desktop client built by desktop/build-client.sh, the nova CLI wheel,
the organisation root CA and SHA256SUMS.

    python3 render.py <Dorj Agent src> <out dir> <server URL>

Edits are pattern-based (file names, version, server address), so a newer upstream page keeps working;
anything that no longer matches fails loudly.
"""
import hashlib
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
src, out, server = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3].rstrip("/")
dist = ROOT / "desktop/dist"
ver = (dist / "VERSION").read_text().strip()          # written by desktop/build-client.sh
files = {"exe": f"Dorj-Agent-Client-Setup-{ver}-x64.exe", "deb": f"Dorj-Agent-Client-{ver}-amd64.deb",
         "appimage": f"Dorj-Agent-Client-{ver}-x86_64.AppImage"}
out.mkdir(parents=True, exist_ok=True)
for f in files.values():
    if not (dist / f).exists():
        sys.exit(f"missing {dist / f}: run desktop/build-client.sh first")
    shutil.copy2(dist / f, out / f)
wheels = sorted((ROOT / "download/dist").glob("dorj_agent_cli-*.whl"))
extras = []
if wheels:
    shutil.copy2(wheels[-1], out / wheels[-1].name)
    extras.append(wheels[-1].name)
shutil.copy2(ROOT / "desktop/org-root-ca.crt", out / "isigpu-root-ca.crt")
extras.append("isigpu-root-ca.crt")
for f in ROOT.joinpath("download").glob("vazirmatn-*.woff2"):
    shutil.copy2(f, out / f.name)
shutil.copy2(ROOT / "web/brand/dorj-wordmark.png", out / "dorj-wordmark.png")
shutil.copy2(ROOT / "web/brand/favicon-64x64.png", out / "dorj-favicon.png")

html = (src / "scripts/download-site/index.html").read_text(encoding="utf-8")
sizes = [(out / f).stat().st_size / 1e6 for f in files.values()]
host = server.split("://", 1)[-1]


def sub(pattern, repl, *, required=True, count=0):
    global html
    html, n = re.subn(pattern, repl, html, count=count)
    if required and n == 0:
        sys.exit(f"download page: pattern not found: {pattern}")


ver_re = r"\d+\.\d+\.\d+(?:[-+]nova\.\d+)?"
sub(rf"/nova/Dorj-Agent-Client-(?:Setup-)?{ver_re}-x64\.exe", files["exe"])
sub(rf"/nova/Dorj-Agent-Client-{ver_re}-win64\.zip", files["exe"], required=False)
sub(rf"/nova/Dorj-Agent-Client-{ver_re}-amd64\.deb", files["deb"])
sub(rf"/nova/Dorj-Agent-Client-{ver_re}-x86_64\.AppImage", files["appimage"])
sub(rf"sudo (?:dpkg -i|apt install \./)\s*Dorj(?:-Agent)?-Client-{ver_re}-amd64\.deb", f"sudo apt install ./{files['deb']}")
sub(rf"Dorj(?:-Agent)?-Client-(?:Setup-)?{ver_re}-x64\.exe", files["exe"])
sub(rf"Dorj(?:-Agent)?-Client-{ver_re}-x86_64\.AppImage", files["appimage"])
sub(rf"v{ver_re}", f"v{ver}")
sub(rf'<b class="ltr">{ver_re}</b>', f'<b class="ltr">{ver}</b>')
sub(r"\d+[–-]\d+ MB", f"{min(sizes):.0f}–{max(sizes):.0f} MB")
sub(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b", host, required=False)
sub(r'از منوی <b>[^<]+</b> خارج شوید[^<]*', 'از منوی <b>Dorj Agent ← خروج از حساب</b> خارج شوید؛ برای سرور دیگر نسخه‌ی مخصوص آن سرور را از همان سرور دانلود کنید.', required=False)
# Platform brand: the customer-facing download page uses the same palette and logo as the login
# page and main UI. Anchors are kept explicit so upstream changes remain visible.
sub(r'<link rel="icon" type="image/svg\+xml" href="[^"]+" />',
    '<link rel="icon" type="image/png" href="dorj-favicon.png" />')
sub(r'<meta name="theme-color" content="[^"]+" />', '<meta name="theme-color" content="#081430" />')
sub(r'<a class="brand" href="#"><span><span class="nc">D</span>orj Agent</span></a>',
    '<a class="brand" href="#" aria-label="Dorj Agent"><img src="dorj-wordmark.png" alt="Dorj Agent" style="width:112px;height:auto"></a>')
sub(r'rgba\(224,128,95,', 'rgba(232,163,23,', required=False)
sub(r'rgba\(139,124,246,', 'rgba(76,110,245,', required=False)
sub(r'#e0805f', '#E8A317', required=False)
sub(r'#8b7cf6', '#4C6EF5', required=False)
# self-hosted Vazirmatn (customers' machines need not reach Google Fonts)
sub(r"<style>\n", "<style>\n@font-face{font-family:'Vazirmatn';src:url(vazirmatn-400.woff2) format('woff2');font-weight:400;font-display:swap}\n"
    "@font-face{font-family:'Vazirmatn';src:url(vazirmatn-600.woff2) format('woff2');font-weight:600 800;font-display:swap}\n", count=1)
if wheels:
    pip_index = "http://172.16.50.89:8083/repository/pypi-proxy/simple"
    pip_host = "172.16.50.89"
    faq_cli = (f'<details>\n      <summary>خط فرمان (CLI) هم دارد؟</summary>\n      <div class="a">بله — <code class="ltr">dorj-agent</code> و <code class="ltr">nova</code> برای ترمینال و CI. '
               f'نصب: <code class="ltr">pip install --index-url {pip_index} --trusted-host {pip_host} {server}/download/{wheels[-1].name}</code> سپس <code class="ltr">dorj-agent login</code>. '
               f'گواهی ریشه‌ی سازمان: <a class="ltr" href="isigpu-root-ca.crt">isigpu-root-ca.crt</a>.</div>\n    </details>\n    ')
    sub(r"<details>\s*<summary>[^<]*به اشتراک AI", faq_cli + "<details>\n      <summary>به اشتراک AI", required=False, count=1)
assert "172.16.40.188" not in html
for legacy in ("#e0805f", "#8b7cf6", "NovaAgent", "OpenHands"):
    if legacy in html:
        sys.exit(f"download page: legacy token remains: {legacy}")
(out / "index.html").write_text(html, encoding="utf-8")
names = sorted(list(files.values()) + extras)
(out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((out / n).read_bytes()).hexdigest()}  {n}\n" for n in names))
print(f"rendered Dorj Agent {ver} download page -> {out}")
