"""Tests for the strict website identity gates (pass/fail core)."""
from __future__ import annotations

from kildespor.identity import (
    RobotRules,
    _contains_orgnr,
    _norm_text,
    _same_site,
)


class TestOrgnrDetection:
    def test_plain_orgnr(self):
        assert _contains_orgnr("Kontakt oss: 925820148", "925820148")

    def test_orgnr_with_spaces(self):
        assert _contains_orgnr("Org.nr: 925 820 148", "925820148")

    def test_orgnr_inside_longer_digit_run_rejected(self):
        # A phone number / 11-digit run that merely CONTAINS the orgnr must
        # NOT verify a site (precision-first: no coincidental matches).
        assert not _contains_orgnr("Ring 92582014855", "925820148")

    def test_wrong_orgnr_not_matched(self):
        assert not _contains_orgnr("925820147", "925820148")

    def test_digit_permutation_not_matched(self):
        assert not _contains_orgnr("258920148", "925820148")


class TestNameNorm:
    def test_case_and_punctuation(self):
        assert _norm_text("7 Fjell Kjeveortopedi AS!") == "7 fjell kjeveortopedi as"

    def test_whitespace_collapses(self):
        assert _norm_text("  Fjell   AS ") == "fjell as"


class TestSameSite:
    def test_www_equivalence(self):
        assert _same_site("https://www.example.no", "https://example.no")

    def test_different_domains(self):
        assert not _same_site("https://example.no", "https://example.org")


class TestRobots:
    def test_disallow_all(self):
        rules = RobotRules(disallow=["/"])
        assert not rules.allows("/")

    def test_specific_path(self):
        rules = RobotRules(disallow=["/private"])
        assert rules.allows("/")
        assert not rules.allows("/private/x")
