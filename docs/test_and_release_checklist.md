# Test and release checklist

```powershell
pip install -r requirements.txt
ruff check app scripts
ruff format --check app scripts
pytest -q
python scripts/check_no_execution.py
python -m scripts.compliance_audit
python -m scripts.mcp_test_client        # MCP smoke test
```
Expected: lint clean, all tests pass, both compliance scans PASS. Then `git status` and `git diff --stat`
and review before committing (commit only when you choose).

## Pre-Monday Go/No-Go Checklist
Mark each GO or NO-GO. Any NO-GO means do not rely on that output.

- [ ] Providers configured (SEC User-Agent set; optional keys present in `.env` only)
- [ ] Provider freshness: dashboard shows no STALE source
- [ ] Sector universe refreshed
- [ ] Peer mapping refreshed
- [ ] Event blackout refreshed for the coming week
- [ ] Price and volume refreshed; latest bar is the last trading day
- [ ] Data-quality warnings reviewed
- [ ] MCP read-only status confirmed (12 tools, all read-only)
- [ ] Compliance scan PASS
- [ ] Dashboard health green
- [ ] Weekly portfolio reset to $1,000,000
- [ ] Estimated score reset for the new week
- [ ] Manual-entry reminder read: you enter every trade yourself
