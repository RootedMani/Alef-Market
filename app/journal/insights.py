"""Rank findings and pick the top 3 for the insight cards. Planned: Week 3."""

from __future__ import annotations

from app.journal.detectors import Finding


def top_findings(findings: list[Finding | None], *, limit: int = 3) -> list[Finding]:
    """Drop Nones, sort by effect size (largest first), keep ``limit``."""
    raise NotImplementedError("Week 3")
