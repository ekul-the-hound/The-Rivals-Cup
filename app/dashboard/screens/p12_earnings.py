"""Streamlit page script."""

from app.dashboard.common import ROOT as _ROOT  # noqa: F401  (puts repo root on sys.path)
from app.dashboard.earnings_view import page_earnings

page_earnings()
