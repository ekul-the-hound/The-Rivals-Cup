"""Dashboard: every page renders without exception and states the required notice."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.dashboard.common import BANNER
from app.dashboard.views import read_audit_lines

SCREENS_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "screens"
SCREENS = sorted(SCREENS_DIR.glob("p*.py"))


def test_ten_pages():
    assert len(SCREENS) == 10


def test_banner_text_is_exact():
    assert (
        BANNER
        == "Research only. Trades must be independently entered manually in Trader View. This system cannot place or manage trades."
    )


@pytest.mark.parametrize("path", SCREENS, ids=lambda p: p.stem)
def test_page_renders_with_banner(path):
    at = AppTest.from_file(str(path), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert BANNER in [w.value for w in at.warning]


def test_journal_requires_confirmation_checkbox():
    at = AppTest.from_file(str(SCREENS_DIR / "p5_journal.py"), default_timeout=120).run()
    assert not at.exception
    labels = [c.label for c in at.checkbox]
    assert any("ALREADY entered" in x for x in labels)


def test_audit_reader(tmp_path):
    f = tmp_path / "a.log"
    f.write_text('noise\n{"kind":"tool_call","tool":"search_news"}\nINFO:x:{"a":1}\n')
    assert [r.get("tool") for r in read_audit_lines(str(f))] == ["search_news", None]
    assert read_audit_lines(str(tmp_path / "missing")) == []


def test_main_navigation_runs():
    main = Path(__file__).resolve().parents[1] / "dashboard" / "main.py"
    at = AppTest.from_file(str(main), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert BANNER in [w.value for w in at.warning]
