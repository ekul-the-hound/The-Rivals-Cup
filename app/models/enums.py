"""Enums mirror supabase/migrations/*_enums_and_helpers.sql (checked by a test)."""

from enum import StrEnum


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class PairStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    APPROVED_FOR_REVIEW = "APPROVED_FOR_REVIEW"
    ACTIVE_MANUALLY = "ACTIVE_MANUALLY"
    CLOSED_MANUALLY = "CLOSED_MANUALLY"
    REJECTED = "REJECTED"


class DataStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    STALE = "STALE"
    MISSING = "MISSING"
    UNVERIFIED = "UNVERIFIED"
    MANUAL = "MANUAL"


class EvidenceQuality(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    DERIVED = "DERIVED"
    UNVERIFIED = "UNVERIFIED"


class CatalystType(StrEnum):
    M_AND_A = "M_AND_A"
    BUYBACK = "BUYBACK"
    OFFERING = "OFFERING"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    MATERIAL_AGREEMENT = "MATERIAL_AGREEMENT"
    REGULATORY = "REGULATORY"
    INSIDER = "INSIDER"
    OTHER = "OTHER"


class ReviewStatus(StrEnum):
    DRAFT = "DRAFT"
    READY_FOR_CLAUDE_REVIEW = "READY_FOR_CLAUDE_REVIEW"
    REVIEWED = "REVIEWED"
    REJECTED = "REJECTED"


class SystemMode(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PAUSED = "PAUSED"
