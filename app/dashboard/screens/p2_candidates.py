"""Streamlit page script."""

from app.dashboard.common import ROOT as _ROOT  # noqa: F401  (puts repo root on sys.path)
from app.dashboard.views import page_candidates

page_candidates()
