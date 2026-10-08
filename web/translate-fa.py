#!/usr/bin/env python3
"""Add Persian (fa) to the Dorj Agent UI translations using our own LLM gateway (2026-10-08).

Reads NovaAgent src/i18n/translation.json ({key: {lang: text}}), translates every key's English
text that has no "fa" yet, validates that {{placeholders}}, <tags> and $variables are preserved,
and writes "fa" back in place. Resumable: keys whose "fa" is missing or still equal to English are (re)translated.

  ISIGPU_API_KEY=... python3 translate-fa.py /workdir/NovaAgent/src/i18n/translation.json

House style (owner, 2026-10-07): natural Persian like Iranian product UIs, short, no "—";
product terms per GLOSSARY. Failed batches are retried key-by-key; anything still invalid keeps
English and is listed at the end for review.
"""
import concurrent.futures as cf
import json
import os
import re
import ssl
import sys
import urllib.request

API = os.environ.get("LLM_API", "https://llm-api.isigpu.local/v1/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "qwen3")
BATCH = 40
GLOSSARY = """Glossary (use exactly):
Dorj Agent = دستیار دُرج; Dorj = دُرج; OpenHands -> دستیار دُرج; agent = دستیار; conversation = گفتگو;
workspace = فضای کار; repository = مخزن; settings = تنظیمات; skill = مهارت; plugin = افزونه;
secret = رمز (secret); model = مدل; task = کار; plan = برنامه; file = فایل; folder = پوشه.
Keep in English: Git, GitHub, GitLab, Bitbucket, commit, branch, pull request, terminal, Docker, API,
API Key, LLM, MCP, VS Code, Jupyter, token, URL, JSON, YAML, CLI, SSH, Markdown, Slack, Jira, Linear."""
SYSTEM = f"""You translate a software product's UI strings from English to Persian (Farsi).
Write natural, concise Persian like modern Iranian software (Persian script, never Finglish).
Do not use the em dash "—". Keep every {{{{placeholder}}}}, <tag>, </tag>, $VAR, %s, URL,
code, file path and keyboard key exactly as in the source. Keep leading/trailing spaces and
punctuation roles (a label ending with ":" keeps ":"). Do not add explanations.
{GLOSSARY}
Input: a JSON object {{key: english}}. Output: ONLY a JSON object with the same keys and the
Persian translations as values."""

TOKEN_RE = re.compile(r"\{\{[^}]+\}\}|</?[a-zA-Z0-9]+[^>]*>|\$[A-Z_]+|%[sd]|https?://\S+")
CTX = ssl._create_unverified_context()  # org CA; the gateway is internal


def tokens(s):
    return sorted(TOKEN_RE.findall(s))


def call(batch):
    body = json.dumps({
        "model": MODEL, "temperature": 0.1, "max_tokens": 8000,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
    }).encode()
    req = urllib.request.Request(API, body, {"Authorization": "Bearer " + os.environ["ISIGPU_API_KEY"],
                                             "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=300) as r:
        text = json.load(r)["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def valid(src, dst):
    return isinstance(dst, str) and dst.strip() != "" and tokens(src) == tokens(dst) and "—" not in dst


def translate(batch):
    good, bad = {}, {}
    try:
        out = call(batch)
    except Exception:
        out = {}
    for k, en in batch.items():
        (good if valid(en, out.get(k)) else bad)[k] = out.get(k) if valid(en, out.get(k)) else en
    if len(batch) > 1 and bad:  # retry the failures one by one
        for k in list(bad):
            g, b = translate({k: batch[k]})
            if g:
                good.update(g); bad.pop(k)
    return good, bad


def main(path):
    data = json.load(open(path, encoding="utf-8"))
    todo = {k: v["en"] for k, v in data.items() if v.get("en") and (not v.get("fa") or v.get("fa") == v["en"])}
    print(f"{len(todo)} strings to translate ({len(data)} total)", flush=True)
    keys = list(todo)
    batches = [{k: todo[k] for k in keys[i:i + BATCH]} for i in range(0, len(keys), BATCH)]
    failed = {}
    with cf.ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "4"))) as ex:
        for n, (good, bad) in enumerate(ex.map(translate, batches), 1):
            for k, fa in good.items():
                data[k]["fa"] = fa
            failed.update(bad)
            if n % 5 == 0 or n == len(batches):
                json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                open(path, "a").write("\n")
                print(f"  batch {n}/{len(batches)} saved, failed so far {len(failed)}", flush=True)
    for k, en in failed.items():  # keep English so the UI never shows an empty string
        data[k]["fa"] = en
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(path, "a").write("\n")
    print(f"done; {len(failed)} kept in English: {list(failed)[:20]}")


if __name__ == "__main__":
    main(sys.argv[1])
