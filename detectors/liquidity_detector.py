"""
OrderBlock Structural Analysis: Liquidity Analyzer & Sweep Detector
File: backend/detectors/liquidity_detector.py
Detects confirmed key liquidity levels and potential liquidity sweeps.
Wick-only pierce beyond confirmed level followed by reclaim is flagged as a Sweep.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from backend.core.schemas import Candle5m
from backend.detectors.swing_detector import SwingPoint, SwingDetector


@dataclass(frozen=True)
class LiquidityLevel:
    """Confirmed structural support, resistance, or liquidity pool level."""
    symbol: str
    price: float
    level_type: str            # "PDH", "PDL", "PDC", "EQUAL_HIGH", "EQUAL_LOW", "SWING_HIGH", "SWING_LOW"
    timestamp: str


@dataclass(frozen=True)
class LiquiditySweepEvent:
    """Potential liquidity sweep event (price breached level but closed inside)."""
    symbol: str
    timestamp: str
    level_price: float
    is_bullish_sweep: bool     # True: swept lows and reclaimed up; False: swept highs and rejected down
    reclaim_price: float
    invalidation_level: float


class LiquidityDetector:
    """Detects liquidity levels and potential liquidity sweeps on 5-minute candles."""

    def __init__(self, equal_tolerance_pct: float = 0.001):
        self.equal_tolerance_pct = equal_tolerance_pct

    def detect_sweeps(
        self,
        symbol: str,
        candles: List[Candle5m],
        swings: List[SwingPoint]
    ) -> List[LiquiditySweepEvent]:
        """Detects potential liquidity sweeps where wicks pierce swing points but bodies reclaim inside."""
        sweeps: List[LiquiditySweepEvent] = []
        if len(candles) < 3:
            return sweeps

        swing_highs = [s for s in swings if s.is_high]
        swing_lows = [s for s in swings if not s.is_high]

        for i, candle in enumerate(candles):
            # Bullish Sweep: Candle wicks below a confirmed swing low (L_i < L_sw) but body closes above (C_i > L_sw)
            for sl in swing_lows:
                if sl.bar_index < i and candle.low < sl.price and candle.close >= sl.price:
                    sweeps.append(LiquiditySweepEvent(
                        symbol=symbol,
                        timestamp=candle.timestamp,
                        level_price=sl.price,
                        is_bullish_sweep=True,
                        reclaim_price=candle.close,
                        invalidation_level=candle.low
                    ))

            # Bearish Sweep: Candle wicks above a confirmed swing high (H_i > H_sw) but body closes below (C_i < H_sw)
            for sh in swing_highs:
                if sh.bar_index < i and candle.high > sh.price and candle.close <= sh.price:
                    sweeps.append(LiquiditySweepEvent(
                        symbol=symbol,
                        timestamp=candle.timestamp,
                        level_price=sh.price,
                        is_bullish_sweep=False,
                        reclaim_price=candle.close,
                        invalidation_level=candle.high
                    ))

        return sweeps
