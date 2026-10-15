"""
Rigorously Tested Edge Cases for SMC Order Block & Swing Detectors
File: backend/tests/test_detectors.py

Tests verify SMC microstructure invariants:
1. Break of Structure (BOS) requires full 5m candle body close beyond swing point.
2. Wick sweep beyond swing high/low is a liquidity sweep and NEVER creates an Order Block.
3. Wick piercing beyond invalidation floor/ceiling is a liquidity sweep and DOES NOT invalidate.
4. Direct invalidation on gap down/up without prior zone mitigation (FRESH -> INVALIDATED).
5. Flat circuit / Doji bars (open==high==low==close) handled cleanly without ZeroDivisionError.
6. Multi-retest touch counting for mitigated zones.
7. Symmetric Bearish Order Block lifecycle (FRESH -> MITIGATED -> INVALIDATED).
"""

from datetime import datetime, timezone, timedelta
from backend.core.schemas import Candle5m, ZoneLifecycleState
from backend.detectors.swing_detector import SwingDetector
from backend.detectors.ob_detector import OrderBlockDetector


def _generate_candles_around_swing() -> list[Candle5m]:
    """Generates a 5m candle sequence forming a clear swing high at 1030.0."""
    t0 = datetime(2026, 10, 14, 9, 15, tzinfo=timezone.utc)
    bars = [
        Candle5m((t0 + timedelta(minutes=0)).isoformat(), open=1000, high=1010, low=995, close=1005),
        Candle5m((t0 + timedelta(minutes=5)).isoformat(), open=1005, high=1015, low=1000, close=1010),
        Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1010, high=1030, low=1005, close=1020),  # Swing High 1030
        Candle5m((t0 + timedelta(minutes=15)).isoformat(), open=1020, high=1025, low=1010, close=1015),
        Candle5m((t0 + timedelta(minutes=20)).isoformat(), open=1015, high=1020, low=1005, close=1010),
    ]
    return bars


def test_bos_body_close_vs_wick_sweep_edge_cases():
    """
    Edge case contrasting body close Break of Structure (BOS) vs wick liquidity sweep.
    - Candle with high 1035 but close 1025 must NOT create an OB (wick sweep).
    - Candle with close 1035 (above 1030) MUST create a Bullish OB.
    """
    detector = OrderBlockDetector(SwingDetector(left_bars=2, right_bars=2))
    candles = _generate_candles_around_swing()
    t0 = datetime.fromisoformat(candles[-1].timestamp)

    # 1. Wick sweep: High reaches 1035, but body closes at 1025
    candles.append(Candle5m((t0 + timedelta(minutes=5)).isoformat(), open=1012, high=1015, low=1000, close=1002))
    candles.append(Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1005, high=1035, low=1005, close=1025))

    zones = detector.detect_order_blocks("RELIANCE", candles)
    assert len([z for z in zones if z.direction == "BULLISH"]) == 0, "Wick sweep must never produce an Order Block"

    # 2. Body close BOS: Close reaches 1035 (above swing high 1030)
    candles.pop()
    candles.append(Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1005, high=1040, low=1005, close=1035))

    zones_bos = detector.detect_order_blocks("RELIANCE", candles)
    bull_obs = [z for z in zones_bos if z.direction == "BULLISH"]
    assert len(bull_obs) == 1
    assert bull_obs[0].state == ZoneLifecycleState.FRESH
    assert bull_obs[0].zone_high == 1012.0
    assert bull_obs[0].zone_low == 1002.0
    assert bull_obs[0].invalidation_level == 1000.0


def test_direct_invalidation_gap_without_mitigation():
    """
    CRITICAL EDGE CASE:
    Price forms an OB, but on the next bar, extreme gap-down opens and closes
    below the invalidation floor (1000.0) without ever touching the zone [1002, 1012].
    Zone must transition directly from FRESH -> INVALIDATED.
    """
    detector = OrderBlockDetector(SwingDetector(left_bars=2, right_bars=2))
    candles = _generate_candles_around_swing()
    t0 = datetime.fromisoformat(candles[-1].timestamp)

    # Origin and displacement BOS
    candles.append(Candle5m((t0 + timedelta(minutes=5)).isoformat(), open=1012, high=1015, low=1000, close=1002))
    candles.append(Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1005, high=1040, low=1005, close=1035))

    # Gap down crash: Open 995, Close 990 (strictly below floor 1000.0)
    candles.append(Candle5m((t0 + timedelta(minutes=15)).isoformat(), open=995, high=998, low=985, close=990))

    zones = detector.detect_order_blocks("RELIANCE", candles)
    assert len(zones) == 1
    assert zones[0].state == ZoneLifecycleState.INVALIDATED
    assert "breached floor" in zones[0].invalidation_reason
    assert zones[0].touch_count == 0, "Direct invalidation must record 0 touches"


def test_wick_sweep_past_invalidation_does_not_falsely_invalidate():
    """
    SMC EDGE CASE:
    During a zone retest, a stop-hunt wick plunges to 985 (below floor 1000.0),
    but the 5-minute candle body closes back safely at 1005 (inside zone).
    The zone MUST NOT be invalidated because only a full body close constitutes invalidation.
    """
    detector = OrderBlockDetector(SwingDetector(left_bars=2, right_bars=2))
    candles = _generate_candles_around_swing()
    t0 = datetime.fromisoformat(candles[-1].timestamp)

    # Origin and displacement BOS
    candles.append(Candle5m((t0 + timedelta(minutes=5)).isoformat(), open=1012, high=1015, low=1000, close=1002))
    candles.append(Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1005, high=1040, low=1005, close=1035))

    # Bar with long wick to 985 (piercing floor 1000), but body close at 1005 (inside zone)
    candles.append(Candle5m((t0 + timedelta(minutes=15)).isoformat(), open=1030, high=1030, low=985, close=1005))

    zones = detector.detect_order_blocks("RELIANCE", candles)
    assert len(zones) == 1
    assert zones[0].state == ZoneLifecycleState.MITIGATED, "Wick piercing floor must mitigate, NOT invalidate"
    assert zones[0].touch_count == 1


def test_flat_circuit_doji_candles_no_zero_division():
    """
    EDGE CASE:
    30 consecutive candles where open == high == low == close (upper/lower circuit freeze).
    Detectors must handle zero price variation gracefully without ZeroDivisionError.
    """
    t0 = datetime(2026, 10, 14, 9, 15, tzinfo=timezone.utc)
    flat_candles = [
        Candle5m((t0 + timedelta(minutes=5 * i)).isoformat(), open=500.0, high=500.0, low=500.0, close=500.0, volume=0.0)
        for i in range(30)
    ]
    detector = OrderBlockDetector(SwingDetector(left_bars=2, right_bars=2))
    zones = detector.detect_order_blocks("CIRCUIT_SYM", flat_candles)
    assert isinstance(zones, list)
    assert len(zones) == 0


def test_multiple_retests_touch_counter_increment():
    """
    Verifies that multiple separate candle tests into the zone increment `touch_count`.
    """
    detector = OrderBlockDetector(SwingDetector(left_bars=2, right_bars=2))
    candles = _generate_candles_around_swing()
    t0 = datetime.fromisoformat(candles[-1].timestamp)

    # OB formed: Zone [1002, 1012]
    candles.append(Candle5m((t0 + timedelta(minutes=5)).isoformat(), open=1012, high=1015, low=1000, close=1002))
    candles.append(Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1005, high=1040, low=1005, close=1035))

    # Touch 1: Low 1006
    candles.append(Candle5m((t0 + timedelta(minutes=15)).isoformat(), open=1030, high=1030, low=1006, close=1020))
    # Bounce
    candles.append(Candle5m((t0 + timedelta(minutes=20)).isoformat(), open=1020, high=1035, low=1018, close=1030))
    # Touch 2: Low 1008
    candles.append(Candle5m((t0 + timedelta(minutes=25)).isoformat(), open=1030, high=1030, low=1008, close=1015))

    zones = detector.detect_order_blocks("RELIANCE", candles)
    assert len(zones) == 1
    assert zones[0].state == ZoneLifecycleState.MITIGATED
    assert zones[0].touch_count == 2


def test_bearish_order_block_lifecycle():
    """
    Symmetric test for Bearish Order Block:
    1. Swing Low formed.
    2. Bearish displacement body closes below swing low -> creates Bearish OB with ceiling.
    3. Retest upward into supply zone -> MITIGATED.
    4. Bullish breakout closing above ceiling -> INVALIDATED with 'breached ceiling'.
    """
    t0 = datetime(2026, 10, 14, 9, 15, tzinfo=timezone.utc)
    bars = [
        Candle5m((t0 + timedelta(minutes=0)).isoformat(), open=1020, high=1025, low=1010, close=1015),
        Candle5m((t0 + timedelta(minutes=5)).isoformat(), open=1015, high=1018, low=1005, close=1010),
        Candle5m((t0 + timedelta(minutes=10)).isoformat(), open=1010, high=1015, low=980, close=990),  # Swing Low at 980
        Candle5m((t0 + timedelta(minutes=15)).isoformat(), open=990, high=1005, low=985, close=1000),
        Candle5m((t0 + timedelta(minutes=20)).isoformat(), open=1000, high=1010, low=995, close=1005),
    ]

    detector = OrderBlockDetector(SwingDetector(left_bars=2, right_bars=2))
    # Bullish pullback candle before downward displacement
    bars.append(Candle5m((t0 + timedelta(minutes=25)).isoformat(), open=1002, high=1015, low=1000, close=1012))
    # Displacement candle closing below swing low 980 -> BOS!
    bars.append(Candle5m((t0 + timedelta(minutes=30)).isoformat(), open=1010, high=1010, low=970, close=975))

    zones = detector.detect_order_blocks("BEAR_SYM", bars)
    bear_obs = [z for z in zones if z.direction == "BEARISH"]
    assert len(bear_obs) == 1
    ob = bear_obs[0]
    assert ob.state == ZoneLifecycleState.FRESH
    assert ob.zone_high == 1012.0
    assert ob.invalidation_level == 1015.0  # High of origin candle is ceiling

    # Retest upward into [1002, 1012] -> MITIGATED
    bars.append(Candle5m((t0 + timedelta(minutes=35)).isoformat(), open=975, high=1008, low=975, close=995))
    zones_mitigated = detector.detect_order_blocks("BEAR_SYM", bars)
    assert zones_mitigated[0].state == ZoneLifecycleState.MITIGATED

    # Invalidation: Body closes above 1015.0
    bars.append(Candle5m((t0 + timedelta(minutes=40)).isoformat(), open=995, high=1025, low=995, close=1020))
    zones_invalidated = detector.detect_order_blocks("BEAR_SYM", bars)
    assert zones_invalidated[0].state == ZoneLifecycleState.INVALIDATED
    assert "breached ceiling" in zones_invalidated[0].invalidation_reason
