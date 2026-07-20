from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from analysis.backtest import build_market_context
from data.market_data import canonical_broker, outcome_from_offer


class MarketLogicTests(unittest.TestCase):
    def test_canonical_broker_uses_lead_broker(self) -> None:
        value = "A1 Capital Yatırım Menkul Değerler A.Ş. Ziraat Yatırım Menkul Değerler A.Ş."
        self.assertEqual(canonical_broker(value), "a1 capital")

    def test_only_last_365_days_count_toward_market_context(self) -> None:
        outcomes = [
            {"listing_date": "2026-07-01", "return_5d_pct": 10, "source_url": "https://example.test/a"},
            {"listing_date": "2025-07-17", "return_5d_pct": 20, "source_url": "https://example.test/b"},
        ]
        context = build_market_context(outcomes, date(2026, 7, 18))
        self.assertEqual(context["overall"]["sample_size"], 1)
        self.assertEqual(context["overall"]["weighted_median_return_5d"], 10)

    def test_unlisted_offer_does_not_create_an_outcome(self) -> None:
        offer = {"ticker": "TEST", "listing_date": "2026-07-18", "ipo_price_tl": 10}
        self.assertIsNone(outcome_from_offer(offer, date(2026, 7, 18)))


if __name__ == "__main__":
    unittest.main()
