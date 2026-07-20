"""Scoring engine ve puanlama mantığı testleri."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from analysis.scoring_engine import (
    _clamp,
    _decision_label,
    _freshness_score,
    _lerp,
    _margin_score,
    _ratio_score_smooth,
    _red_flags,
    assess_offer,
)


class LerpTests(unittest.TestCase):
    def test_lerp_midpoint(self):
        self.assertAlmostEqual(_lerp(5, 0, 10, 0, 100), 50)

    def test_lerp_boundaries(self):
        self.assertAlmostEqual(_lerp(0, 0, 10, 20, 80), 20)
        self.assertAlmostEqual(_lerp(10, 0, 10, 20, 80), 80)


class ClampTests(unittest.TestCase):
    def test_clamp_within_range(self):
        self.assertEqual(_clamp(50), 50)

    def test_clamp_below(self):
        self.assertEqual(_clamp(-5), 0)

    def test_clamp_above(self):
        self.assertEqual(_clamp(120), 100)


class SmoothScoreTests(unittest.TestCase):
    def test_at_first_band(self):
        result = _ratio_score_smooth(0.1, [(0.3, 90), (0.6, 72), (1.0, 45)])
        self.assertEqual(result, 90)

    def test_between_bands_is_interpolated(self):
        result = _ratio_score_smooth(0.45, [(0.3, 90), (0.6, 72)])
        self.assertIsNotNone(result)
        self.assertGreater(result, 72)
        self.assertLess(result, 90)

    def test_none_returns_none(self):
        self.assertIsNone(_ratio_score_smooth(None, [(1, 50)]))


class FreshnessTests(unittest.TestCase):
    def test_recent_data_scores_100(self):
        result = _freshness_score("2026-07-01", date(2026, 7, 18))
        self.assertEqual(result, 100)

    def test_very_old_data_scores_low(self):
        result = _freshness_score("2024-01-01", date(2026, 7, 18))
        self.assertEqual(result, 10)

    def test_none_input(self):
        self.assertIsNone(_freshness_score(None, date(2026, 7, 18)))


class MarginScoreTests(unittest.TestCase):
    def test_positive_margin(self):
        result = _margin_score(0.15)
        self.assertIsNotNone(result)
        self.assertGreater(result, 40)

    def test_negative_margin_penalized(self):
        result = _margin_score(-0.1)
        self.assertIsNotNone(result)
        self.assertLess(result, 25)

    def test_none_margin(self):
        self.assertIsNone(_margin_score(None))


class RedFlagTests(unittest.TestCase):
    def test_high_debt_flags(self):
        offer = {"financials": {"net_debt_to_equity": 1.5, "net_debt_to_ebitda": 5}}
        flags = _red_flags(offer)
        self.assertTrue(any("borç/özkaynak" in f for f in flags))
        self.assertTrue(any("FAVÖK" in f for f in flags))

    def test_no_documents_flag(self):
        offer = {"documents": [], "metric_sources": []}
        flags = _red_flags(offer)
        self.assertTrue(any("kaynak belge" in f for f in flags))

    def test_low_float_flag(self):
        offer = {"float_pct": 5}
        flags = _red_flags(offer)
        self.assertTrue(any("açıklık" in f.lower() for f in flags))


class DecisionLabelTests(unittest.TestCase):
    def test_low_coverage_returns_data_missing(self):
        label = _decision_label(60, 40, [], True)
        self.assertIn("VERİ", label)

    def test_high_score_and_coverage(self):
        label = _decision_label(75, 80, [], True)
        self.assertEqual(label, "ÖNCELİKLİ İNCELEME")

    def test_many_critical_flags_triggers_risk(self):
        flags = ["🔴 flag1", "🔴 flag2"]
        label = _decision_label(70, 80, flags, True)
        self.assertIn("RİSK", label)


class AssessOfferTests(unittest.TestCase):
    def test_produces_all_assessment_keys(self):
        offer = {
            "ticker": "TEST",
            "company": "Test A.Ş.",
            "start_date": "2026-07-20",
            "calendar_date_text": "20-22 Temmuz 2026",
            "financials": {},
            "documents": [],
        }
        market = {"overall": {"is_ready": False, "sample_size": 0}}
        result = assess_offer(offer, market, date(2026, 7, 18))
        assessment = result["assessment"]
        required_keys = [
            "evidence_score", "known_data_score", "evidence_coverage_pct",
            "decision_label", "components", "red_flags", "review_questions",
        ]
        for key in required_keys:
            self.assertIn(key, assessment, f"Missing key: {key}")

    def test_financial_data_increases_coverage(self):
        offer_empty = {
            "ticker": "A", "company": "A", "start_date": "2026-07-20",
            "calendar_date_text": "test", "financials": {}, "documents": [],
        }
        offer_rich = {
            "ticker": "B", "company": "B", "start_date": "2026-07-20",
            "calendar_date_text": "test",
            "financials": {
                "net_debt_to_equity": 0.4, "net_debt_to_ebitda": 1.8,
                "current_ratio": 1.5, "net_margin": 0.12,
                "as_of": "2026-06-30",
            },
            "documents": [],
        }
        market = {"overall": {"is_ready": False, "sample_size": 0}}
        r1 = assess_offer(offer_empty, market, date(2026, 7, 18))
        r2 = assess_offer(offer_rich, market, date(2026, 7, 18))
        self.assertGreater(
            r2["assessment"]["evidence_coverage_pct"],
            r1["assessment"]["evidence_coverage_pct"],
        )


if __name__ == "__main__":
    unittest.main()
