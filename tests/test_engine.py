"""
Unit tests for backend core quantitative engine
File: backend/tests/test_engine.py
SYNTHETIC TEST DATA: All test inputs and historical bars in this file are synthetic.
Zero external runtime dependencies beyond unittest / stdlib.
"""

import unittest
from datetime import datetime, timezone

from backend.core.schemas import (
    DailyBar,
    IntradaySnapshot,
    ContractUniverse,
    EngineConfig,
    DataQualityStatus,
    CycleQuality,
)
from backend.core.engine import HistoricalBaselineEngine, StrengthRankingEngine


class TestHistoricalBaselineEngine(unittest.TestCase):
    """Tests the 50-day rolling baseline calculation and data validation rules."""

    def test_empty_history(self):
        telemetry = HistoricalBaselineEngine.validate_and_compute("RELIANCE", [])
        self.assertEqual(telemetry.status, DataQualityStatus.INSUFFICIENT_HISTORY)
        self.assertEqual(telemetry.sessions_evaluated, 0)
        self.assertFalse(telemetry.meets_full_lookback)

    def test_malformed_symbol(self):
        telemetry = HistoricalBaselineEngine.validate_and_compute("invalid symbol!", [])
        self.assertEqual(telemetry.status, DataQualityStatus.MALFORMED_SYMBOL_ERROR)

    def test_invalid_bar_geometry(self):
        bad_bars = [
            DailyBar(trading_date="2026-01-01", open=100.0, high=90.0, low=110.0, close=95.0)
        ]
        telemetry = HistoricalBaselineEngine.validate_and_compute("TCS", bad_bars, min_required=1)
        self.assertEqual(telemetry.status, DataQualityStatus.INVALID_BAR_GEOMETRY)

    def test_duplicate_date_error(self):
        dup_bars = [
            DailyBar(trading_date="2026-01-01", open=100.0, high=105.0, low=95.0, close=102.0),
            DailyBar(trading_date="2026-01-01", open=102.0, high=108.0, low=98.0, close=104.0),
        ]
        telemetry = HistoricalBaselineEngine.validate_and_compute("INFY", dup_bars, min_required=1)
        self.assertEqual(telemetry.status, DataQualityStatus.DUPLICATE_DATE_ERROR)

    def test_valid_50_sessions_baseline_computation(self):
        # Generate 50 synthetic valid trading bars
        bars = []
        for i in range(1, 51):
            day_str = f"2026-01-{i:02d}" if i <= 31 else f"2026-02-{i-31:02d}"
            bars.append(DailyBar(
                trading_date=day_str,
                open=1000.0 + i,
                high=1020.0 + i,
                low=990.0 + i,
                close=1010.0 + i,
                prev_close=1005.0 + i if i > 1 else None
            ))

        telemetry = HistoricalBaselineEngine.validate_and_compute("RELIANCE", bars, lookback=50, min_required=40)
        self.assertEqual(telemetry.status, DataQualityStatus.VALID)
        self.assertEqual(telemetry.sessions_evaluated, 50)
        self.assertTrue(telemetry.meets_full_lookback)
        self.assertFalse(telemetry.was_reordered)
        self.assertGreater(telemetry.atr_50, 0.0)
        self.assertGreater(telemetry.adm_50, 0.0)
        self.assertGreater(telemetry.ahr_50, 0.0)
        self.assertGreater(telemetry.ade_bull_50, 0.0)
        self.assertGreater(telemetry.ade_bear_50, 0.0)


class TestStrengthRankingEngine(unittest.TestCase):
    """Tests the strength formula, directional imbalance filters, near-zero guard, and ranking."""

    def setUp(self):
        self.config = EngineConfig()
        self.engine = StrengthRankingEngine(self.config)
        self.universe = ContractUniverse(
            as_of_date="2026-10-12",
            symbols=("RELIANCE", "TCS", "INFY")
        )

    def test_directional_imbalance_multiplication(self):
        # Bullish eligibility: delta_up >= 2 * delta_down
        # LTP = 105, Open = 100, High = 106, Low = 98 (delta_up = 6, delta_down = 2 -> 6 >= 2*2=4 -> True)
        snap = IntradaySnapshot(
            symbol="RELIANCE",
            timestamp="2026-10-12T10:00:00+05:30",
            open=100.0, high=106.0, low=98.0, ltp=105.0, prev_close=99.0
        )
        self.assertTrue(self.engine.check_imbalance(snap, 6.0, 2.0, "BULLISH"))
        self.assertFalse(self.engine.check_imbalance(snap, 6.0, 2.0, "BEARISH"))

        # Adverse move too large: Low = 96 -> delta_down = 4 -> 6 < 2*4=8 -> False
        self.assertFalse(self.engine.check_imbalance(snap, 6.0, 4.0, "BULLISH"))

    def test_near_zero_guard_applied_transparently(self):
        # Baseline near zero -> guard applied
        bars = [
            DailyBar(trading_date=f"2026-01-{i:02d}", open=100.0, high=100.05, low=99.95, close=100.0)
            for i in range(1, 51)
        ]
        telemetry = HistoricalBaselineEngine.validate_and_compute("TCS", bars, lookback=50, min_required=40)
        raw_d, eff_d, thresh, guard = self.engine.resolve_denominator(telemetry, "ATR", 100.0, "BULLISH")
        self.assertTrue(guard)
        self.assertGreater(eff_d, raw_d)
        self.assertEqual(eff_d, thresh)

    def test_deterministic_three_tier_sort(self):
        # 3 snapshots with identical scores or tie breaks
        now_iso = "2026-10-12T10:00:00+05:30"
        snaps = [
            IntradaySnapshot(symbol="INFY", timestamp=now_iso, open=100.0, high=110.0, low=99.0, ltp=108.0, prev_close=100.0),
            IntradaySnapshot(symbol="RELIANCE", timestamp=now_iso, open=100.0, high=110.0, low=99.0, ltp=108.0, prev_close=100.0),
            IntradaySnapshot(symbol="TCS", timestamp=now_iso, open=100.0, high=105.0, low=99.0, ltp=104.0, prev_close=100.0),
        ]
        # Equal baselines
        baselines = {}
        for sym in ["RELIANCE", "TCS", "INFY"]:
            bars = [
                DailyBar(
                    trading_date=f"2026-01-{i:02d}" if i <= 31 else f"2026-02-{i-31:02d}",
                    open=100.0, high=105.0, low=95.0, close=100.0
                )
                for i in range(1, 51)
            ]
            baselines[sym] = HistoricalBaselineEngine.validate_and_compute(sym, bars)

        board_snap = self.engine.process_universe(now_iso, self.universe, snaps, baselines)
        self.assertEqual(board_snap.published_bullish_count, 3)
        # INFY and RELIANCE have identical score and delta C%, so alphabetical tie break should put INFY before RELIANCE
        self.assertEqual(board_snap.bullish_board[0].symbol, "INFY")
        self.assertEqual(board_snap.bullish_board[1].symbol, "RELIANCE")
        self.assertEqual(board_snap.bullish_board[2].symbol, "TCS")


if __name__ == "__main__":
    unittest.main()
