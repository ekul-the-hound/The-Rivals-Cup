"""Local research dashboard.   python -m streamlit run app/dashboard/main.py   (from the repo root)

Research only. Trades must be independently entered manually in Trader View. This system cannot
place or manage trades.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

PAGES = [
    ("screens/p1_monday_builder.py", "1. Monday portfolio builder"),
    ("screens/p2_candidates.py", "2. Peer-pair candidates"),
    ("screens/p3_pair_detail.py", "3. Pair research detail"),
    ("screens/p4_market.py", "4. Market / sector dashboard"),
    ("screens/p5_journal.py", "5. Manual portfolio journal"),
    ("screens/p6_score.py", "6. Estimated score & exposure"),
    ("screens/p7_quality.py", "7. Data quality"),
    ("screens/p8_mcp.py", "8. MCP status / audit"),
    ("screens/p9_compliance.py", "9. Compliance status"),
    ("screens/p10_target_universe.py", "10. Target-sector universe"),
    ("screens/p11_leaders_laggards.py", "11. Leaders & laggards"),
    ("screens/p12_earnings.py", "12. Earnings (long-focused)"),
]

st.set_page_config(page_title="WSR research (manual)", layout="wide")
nav = st.navigation([st.Page(p, title=t, url_path=p.split("/")[1].split("_")[0]) for p, t in PAGES])
nav.run()
