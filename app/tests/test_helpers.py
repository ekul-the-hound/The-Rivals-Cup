import pytest

from app.services.audit import redact_args
from app.services.validation.guards import ResearchOnlyViolation, assert_research_only


def test_redact():
    out = redact_args({"ticker": "KO", "api_key": "abc", "n": [{"Authorization": "x"}]})
    assert out == {"ticker": "KO", "api_key": "[REDACTED]", "n": [{"Authorization": "[REDACTED]"}]}


def test_signal_sending_cannot_be_enabled():
    assert_research_only(False)
    with pytest.raises(ResearchOnlyViolation):
        assert_research_only(True)
