from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class CandidateStatus(StrEnum):
    NEW = "new"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    CORRECTED = "corrected"
    WITHDRAWN = "withdrawn"


class EventStatus(StrEnum):
    ANNOUNCED = "announced"
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    ONGOING = "ongoing"
    UNKNOWN = "unknown"


class EvidenceRole(StrEnum):
    PRIMARY = "primary"
    CORROBORATING = "corroborating"
    CONTEXT = "context"
    CONTRADICTING = "contradicting"
    CORRECTION = "correction"


class EventDomain(StrEnum):
    FOOD_SECURITY = "food_security"
    HEALTHCARE_ACCESS = "healthcare_access"
    WORKFORCE = "workforce"
    IMMIGRATION_ENFORCEMENT = "immigration_enforcement"
    CONFLICT_HUMANITARIAN = "conflict_humanitarian"
