from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from analysis.backtest import build_broker_leaderboard, build_market_context


class BrokerStabilityTests(unittest.TestCase):
    def test_requires_three_observations_and_returns_a_score(self) -> None:
        outcomes = [
            {"listing_date": f"2026-0{month}-01", "return_5d_pct": result, "max_drawdown_20d_pct": 5, "source_url": "https://example.test", "broker_key": "test broker"}
            for month, result in ((5, 4), (6, 6), (7, 5))
        ]
        rows = build_broker_leaderboard(outcomes, date(2026, 7, 18))
        self.assertEqual(rows[0]["broker_key"], "test broker")
        self.assertIsNotNone(rows[0]["stability_score"])


if __name__ == "__main__":
    unittest.main()
