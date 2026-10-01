"""Repository compliance audit: fails if execution or platform-integration code appears."""

from app.compliance.rules import RULES, AuditReport, audit

__all__ = ["RULES", "AuditReport", "audit"]
