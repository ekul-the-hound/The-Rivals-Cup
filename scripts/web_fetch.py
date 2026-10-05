"""Download public web pages as plain text so they can be read from the shared folder.

  python -m scripts.web_fetch https://example.com/a https://example.com/b
  python -m scripts.web_fetch --file urls.txt --out data/generated/web

Plain HTTP GETs only (no browser automation, no logins). It identifies itself with your
SEC_USER_AGENT / WEB_USER_AGENT, obeys robots.txt, waits between requests, and skips any page the
site's robots.txt disallows. Output: <out>/<slug>.txt per page plus index.json. Pages that need
JavaScript or a login come back mostly empty; that is reported, not worked around.
Read-only research: it has no connection to a broker, Trader View or Wall Street Rivals.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.services.providers.sec import html_to_excerpt

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2_000_000


def slug(url: str) -> str:
    u = urlparse(url)
    base = re.sub(r"[^a-z0-9]+", "-", (u.netloc + u.path).lower()).strip("-")[:70]
    return f"{base}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


def allowed(
    client: httpx.Client, url: str, ua: str, cache: dict[str, urllib.robotparser.RobotFileParser]
) -> bool:
    u = urlparse(url)
    root = f"{u.scheme}://{u.netloc}"
    if root not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = client.get(f"{root}/robots.txt")
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])
        cache[root] = rp
    return cache[root].can_fetch(ua, url)


def fetch_all(
    urls: list[str],
    out: Path,
    ua: str,
    delay: float = 2.0,
    transport: httpx.BaseTransport | None = None,
) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}
    with httpx.Client(
        headers={"User-Agent": ua}, timeout=25, follow_redirects=True, transport=transport
    ) as client:
        for i, url in enumerate(urls):
            rec = {
                "url": url,
                "fetched_at": datetime.now(UTC).isoformat(),
                "status": None,
                "file": None,
                "chars": 0,
                "note": "",
            }
            try:
                if not allowed(client, url, ua, robots):
                    rec["note"] = "skipped: robots.txt disallows this page"
                else:
                    resp = client.get(url)
                    rec["status"] = resp.status_code
                    if resp.status_code >= 400:
                        rec["note"] = f"HTTP {resp.status_code}"
                    else:
                        raw = resp.text[:MAX_BYTES]
                        text = (
                            raw
                            if "json" in resp.headers.get("content-type", "")
                            else html_to_excerpt(raw, 60_000)
                        )
                        rec["chars"] = len(text)
                        if rec["chars"] < 300:
                            rec["note"] = (
                                "very little text: page probably needs JavaScript or a login"
                            )
                        f = out / f"{slug(url)}.txt"
                        f.write_text(
                            f"SOURCE: {url}\nFETCHED: {rec['fetched_at']}\n\n{text}\n",
                            encoding="utf-8",
                        )
                        rec["file"] = f.name
            except httpx.HTTPError as exc:
                rec["note"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            results.append(rec)
            if i < len(urls) - 1 and delay:
                time.sleep(delay)
    (out / "index.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--file", help="text file with one URL per line")
    ap.add_argument("--out", default=str(ROOT / "data" / "generated" / "web"))
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    a = ap.parse_args(argv)
    urls = list(a.urls)
    if a.file:
        urls += [
            x.strip()
            for x in Path(a.file).read_text(encoding="utf-8").splitlines()
            if x.strip().startswith("http")
        ]
    urls = list(dict.fromkeys(urls))
    if not urls:
        print("Give at least one URL (or --file).", file=sys.stderr)
        return 2
    ua = get_settings().effective_web_user_agent
    res = fetch_all(urls, Path(a.out), ua, a.delay)
    for r in res:
        print(
            f"{'OK  ' if r['file'] else 'SKIP'} {r['url']}  {r['note'] or str(r['chars']) + ' chars'}"
        )
    print(f"index: {Path(a.out) / 'index.json'}")
    return 0 if any(r["file"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
