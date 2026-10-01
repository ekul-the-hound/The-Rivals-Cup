"""Final repository compliance audit. Exit code 1 if any execution/integration code is found.

python -m scripts.compliance_audit            # human-readable
python -m scripts.compliance_audit --json
"""

import argparse
import sys
from pathlib import Path

from app.compliance.rules import audit

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    rep = audit(a.root)
    print(rep.to_json() if a.json else rep.render())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
