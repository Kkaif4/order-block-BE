"""
OrderBlock Structural Analysis: Order Block Detector & Lifecycle Engine
File: backend/detectors/ob_detector.py

Identifies institutional Order Blocks on 5-minute candles:
- Break of Structure (BOS) requires full candle body close beyond prior confirmed swing level.
- Wick piercing swing level without body close is treated as a Liquidity Sweep, NOT BOS.
- Strict confirmation offset (i > sh.bar_index + right_bars) to eliminate look-ahead bias.
- Strict lifecycle state machine: FRESH -> MITIGATED -> INVALIDATED (or direct INVALIDATED).
Adheres strictly to docs/BE_rulebook.md Section 3.3.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Literal
from backend.core.schemas import Candle5m, ZoneLifecycleState
from backend.detectors.swing_detector import SwingPoint, SwingDetector


@dataclass
class OrderBlockZone:
    """Institutional Order Block zone with active lifecycle."""
    zone_id: str
    symbol: str
    direction: Literal["BULLISH", "BEARISH"]
    detection_time: str
    confirmation_time: str
    zone_high: float
    zone_low: float
    invalidation_level: float
    state: ZoneLifecycleState = ZoneLifecycleState.FRESH
    touch_count: int = 0
    invalidation_reason: Optional[str] = None
    displacement_ratio: float = 0.0


class OrderBlockDetector:
    """Detects and maintains Order Block zones from 5-minute OHLCV sequences."""

    def __init__(self, swing_detector: Optional[SwingDetector] = None, min_displacement_atr: float = 1.5):
        self.swing_detector = swing_detector or SwingDetector(left_bars=2, right_bars=2)
        self.min_displacement_atr = min_displacement_atr

    def detect_order_blocks(
        self,
        symbol: str,
        candles: List[Candle5m],
        existing_zones: Optional[List[OrderBlockZone]] = None
    ) -> List[OrderBlockZone]:
        """Scans candles for new Order Blocks and updates existing zones' lifecycle states."""
        zones: List[OrderBlockZone] = list(existing_zones or [])
        if len(candles) < 5:
            return zones

        swings = self.swing_detector.detect_swings(candles)
        swing_highs = [s for s in swings if s.is_high]
        swing_lows = [s for s in swings if not s.is_high]
        right_bars = self.swing_detector.right_bars

        # Scan for Break of Structure (BOS) on recent candles
        for i in range(1, len(candles)):
            curr = candles[i]
            prev = candles[i - 1]

            # 1. Bullish Order Block Candidate:
            # Requires a bullish candle body close breaking above a prior confirmed swing high.
            # Must strictly be confirmed after swing confirmation window (i > sh.bar_index + right_bars).
            for sh in swing_highs:
                if i > (sh.bar_index + right_bars) and prev.close <= sh.price:
                    # Check BOS condition: body close strictly above swing high (C_i > H_sw)
                    if curr.close > sh.price:
                        # Find the preceding bearish candle that originated this move
                        ob_candidate = None
                        for k in range(i - 1, max(0, i - 5), -1):
                            if candles[k].close < candles[k].open:
                                ob_candidate = candles[k]
                                break

                        if ob_candidate:
                            zone_id = f"{symbol}-BULL-{ob_candidate.timestamp}"
                            if not any(z.zone_id == zone_id for z in zones):
                                z_high = max(ob_candidate.open, ob_candidate.close)
                                z_low = min(ob_candidate.open, ob_candidate.close)
                                zones.append(OrderBlockZone(
                                    zone_id=zone_id,
                                    symbol=symbol,
                                    direction="BULLISH",
                                    detection_time=ob_candidate.timestamp,
                                    confirmation_time=curr.timestamp,
                                    zone_high=z_high,
                                    zone_low=z_low,
                                    invalidation_level=ob_candidate.low,  # Floor is the low of the OB candle
                                    state=ZoneLifecycleState.FRESH,
                                    touch_count=0
                                ))

            # 2. Bearish Order Block Candidate:
            # Requires a bearish candle body close breaking below a prior confirmed swing low.
            for sl in swing_lows:
                if i > (sl.bar_index + right_bars) and prev.close >= sl.price:
                    if curr.close < sl.price:
                        ob_candidate = None
                        for k in range(i - 1, max(0, i - 5), -1):
                            if candles[k].close > candles[k].open:
                                ob_candidate = candles[k]
                                break

                        if ob_candidate:
                            zone_id = f"{symbol}-BEAR-{ob_candidate.timestamp}"
                            if not any(z.zone_id == zone_id for z in zones):
                                z_high = max(ob_candidate.open, ob_candidate.close)
                                z_low = min(ob_candidate.open, ob_candidate.close)
                                zones.append(OrderBlockZone(
                                    zone_id=zone_id,
                                    symbol=symbol,
                                    direction="BEARISH",
                                    detection_time=ob_candidate.timestamp,
                                    confirmation_time=curr.timestamp,
                                    zone_high=z_high,
                                    zone_low=z_low,
                                    invalidation_level=ob_candidate.high,  # Ceiling is the high of the OB candle
                                    state=ZoneLifecycleState.FRESH,
                                    touch_count=0
                                ))

        # Update Zone Lifecycle strictly following: FRESH -> MITIGATED -> INVALIDATED
        for z in zones:
            if z.state == ZoneLifecycleState.INVALIDATED:
                continue

            for candle in candles:
                if candle.timestamp <= z.confirmation_time:
                    continue

                if z.direction == "BULLISH":
                    # Direct Invalidation: Single candle pierce beyond invalidation floor
                    if candle.close < z.invalidation_level:
                        z.state = ZoneLifecycleState.INVALIDATED
                        z.invalidation_reason = f"5m body close {candle.close} breached floor {z.invalidation_level}"
                        break
                    # Zone entered without breaching floor -> MITIGATED
                    elif candle.low <= z.zone_high and candle.close >= z.zone_low:
                        z.touch_count += 1
                        z.state = ZoneLifecycleState.MITIGATED

                elif z.direction == "BEARISH":
                    if candle.close > z.invalidation_level:
                        z.state = ZoneLifecycleState.INVALIDATED
                        z.invalidation_reason = f"5m body close {candle.close} breached ceiling {z.invalidation_level}"
                        break
                    elif candle.high >= z.zone_low and candle.close <= z.zone_high:
                        z.touch_count += 1
                        z.state = ZoneLifecycleState.MITIGATED

        return zones
