"""Fill in .env for the earnings module without ever echoing a secret.

  python -m scripts.setup_earnings_env --name "Luke Brunson" --email you@example.com
  python -m scripts.setup_earnings_env --name "Luke Brunson" --email you@example.com --no-prompt

What it sets (existing non-empty values are kept unless you pass --overwrite):
  SEC_USER_AGENT      "Your Name you@example.com"   (SEC requires it; not a secret)
  OPTIONS_IV_ENABLED  true                          (options-implied move and put/call skew)
  FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, POLITICIAN_TRADES_URL
                      asked with hidden input; press Enter to skip any of them.

Get the free keys yourself (the sign-ups need your own email and password):
  Finnhub        https://finnhub.io/register          (analysts, earnings history, calendar)
  Alpha Vantage  https://www.alphavantage.co/support/#api-key  (backup calendar, 25 calls/day)
.env is git-ignored. Never commit it.
"""

import argparse
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECRET_PROMPTS = {
    "FINNHUB_API_KEY": "Finnhub API key (finnhub.io/register)",
    "ALPHA_VANTAGE_API_KEY": "Alpha Vantage API key (alphavantage.co/support/#api-key)",
    "POLITICIAN_TRADES_URL": "Politician-trades JSON feed URL (optional, may contain a key)",
}
HEADER = "# --- Earnings module (docs/earnings.md); written by scripts/setup_earnings_env.py ---"


def apply_env(text: str, updates: dict[str, str], overwrite: bool = False) -> tuple[str, list[str]]:
    """Returns (new text, names of the keys that changed). Preserves every other line."""
    lines = text.splitlines()
    changed: list[str] = []
    for key, value in updates.items():
        if not value:
            continue
        pat = re.compile(rf"^\s*{re.escape(key)}\s*=(.*)$")
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                if m.group(1).strip() and not overwrite:
                    break  # keep what is there
                lines[i] = f"{key}={value}"
                changed.append(key)
                break
        else:
            if HEADER not in lines:
                lines += ["", HEADER]
            lines.append(f"{key}={value}")
            changed.append(key)
    return "\n".join(lines) + "\n", changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--name", required=True, help='your name, e.g. "Luke Brunson"')
    ap.add_argument("--email", required=True, help="contact email for the SEC user agent")
    ap.add_argument("--env", default=str(ROOT / ".env"), help="path to the .env file")
    ap.add_argument("--overwrite", action="store_true", help="replace values that are already set")
    ap.add_argument("--no-prompt", action="store_true", help="do not ask for keys")
    a = ap.parse_args(argv)
    if not EMAIL.match(a.email) or not a.name.strip():
        print("Give a real name and email for the SEC user agent.", file=sys.stderr)
        return 2
    updates = {"SEC_USER_AGENT": f"{a.name.strip()} {a.email}", "OPTIONS_IV_ENABLED": "true"}
    if not a.no_prompt:
        for key, label in SECRET_PROMPTS.items():
            val = getpass.getpass(f"{label} [Enter to skip]: ").strip()
            if val:
                updates[key] = val
    path = Path(a.env)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new, changed = apply_env(old, updates, a.overwrite)
    if changed:
        path.write_text(new, encoding="utf-8")
    print(f"{path}: updated {', '.join(changed) if changed else 'nothing (already set)'}")
    still = [k for k in ("FINNHUB_API_KEY",) if not re.search(rf"^{k}=\S", new, re.M)]
    if still:
        print("Still missing (free): " + ", ".join(still) + ". The scan needs it for the calendar.")
    print("Next: python -m scripts.earnings_scan --live-check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
