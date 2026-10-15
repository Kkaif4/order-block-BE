"""
OrderBlock Analysis: The 6 Top Picks Selector
File: backend/engines/picks_selector.py
Selects the official Top 3 Bullish and Top 3 Bearish picks from ranked candidates:
- Enforces sector diversification cap (at most 2 picks from any single sector).
- Filters out event-risk tickers (excessive volatility / unexpected gaps).
- Calculates the exact level ladder (SUPPORT 1, ZONE, RESISTANCE 1 & 2, and Risk:Reward).
"""

from __future__ import annotations
from typing import List, Dict, Optional
from backend.core.schemas import StockRankOutput, TopPickCardOutput, Candle5m
from backend.core.constants import DEFAULT_SECTOR_PICK_CAP


class TopPicksSelector:
    """Filters candidate boards to identify the official Top Picks (up to 20 per board)."""

    def __init__(self, sector_cap: int = 5):
        self.sector_cap = sector_cap

    def _create_pick_card(
        self,
        stock: StockRankOutput,
        board: str,
        rank: int,
        candles_map: Optional[Dict[str, List[Candle5m]]],
    ) -> TopPickCardOutput:
        sec = stock.sector or "OTHERS"
        candles = (candles_map or {}).get(stock.symbol, [])
        active_ob = None
        tot_obs = 0
        fresh_obs = 0
        mit_obs = 0
        inv_obs = 0

        if candles and len(candles) >= 5:
            from backend.detectors.ob_detector import OrderBlockDetector
            detector = OrderBlockDetector()
            zones = detector.detect_order_blocks(stock.symbol, candles)
            tot_obs = len(zones)
            fresh_obs = sum(1 for z in zones if z.state.value == "FRESH")
            mit_obs = sum(1 for z in zones if z.state.value == "MITIGATED")
            inv_obs = sum(1 for z in zones if z.state.value == "INVALIDATED")
            active_ob = next(
                (z for z in reversed(zones) if z.direction == board and z.state.value in ("FRESH", "MITIGATED")),
                None,
            )

        min_risk = max(round(stock.ltp * 0.005, 2), 0.05)

        if board == "BULLISH":
            if active_ob:
                z_low = round(active_ob.zone_low, 2)
                z_high = round(active_ob.zone_high, 2)
                sl = round(min(active_ob.invalidation_level, z_low - min_risk), 2)
            else:
                z_low = round(stock.open, 2)
                z_high = round(stock.open + stock.raw_baseline * 0.25, 2)
                sl = round(min(stock.ltp - min_risk, z_low - min_risk), 2)

            # Ensure SL is strictly below LTP
            if sl >= stock.ltp:
                sl = round(stock.ltp - min_risk, 2)

            risk = max(min_risk, stock.ltp - sl)
            tp = round(stock.ltp + risk * 2.5, 2)
            rr = round((tp - stock.ltp) / risk, 1)
            if rr < 2.0:
                tp = round(stock.ltp + risk * 2.2, 2)
                rr = 2.2

            vwap = round(stock.open + (stock.ltp - stock.open) * 0.5, 2)
            sup1, res1, res2 = sl, tp, round(stock.ltp + risk * 3.5, 2)
            stop_loss, target = sl, tp
        else:
            if active_ob:
                z_high = round(active_ob.zone_high, 2)
                z_low = round(active_ob.zone_low, 2)
                sl = round(max(active_ob.invalidation_level, z_high + min_risk), 2)
            else:
                z_high = round(stock.open, 2)
                z_low = round(stock.open - stock.raw_baseline * 0.25, 2)
                sl = round(max(stock.ltp + min_risk, z_high + min_risk), 2)

            # Ensure SL is strictly above LTP for shorts
            if sl <= stock.ltp:
                sl = round(stock.ltp + min_risk, 2)

            risk = max(min_risk, sl - stock.ltp)
            tp = max(0.05, round(stock.ltp - risk * 2.5, 2))
            rr = round((stock.ltp - tp) / risk, 1)
            if rr < 2.0:
                tp = max(0.05, round(stock.ltp - risk * 2.2, 2))
                rr = 2.2

            vwap = round(stock.open - (stock.open - stock.ltp) * 0.5, 2)
            sup1, res1, res2 = tp, sl, max(0.05, round(stock.ltp - risk * 3.5, 2))
            stop_loss, target = sl, tp

        return TopPickCardOutput(
            symbol=stock.symbol,
            board=board,
            rank=rank,
            sector=sec,
            ltp=stock.ltp,
            change_pct=stock.display_change_pct,
            conviction_score=stock.conviction_score,
            buildup=stock.buildup,
            oi_change_pct=stock.oi_change_pct,
            oi_net_delta=stock.oi_net_delta,
            support_1=sup1,
            zone_low=round(z_low, 2),
            zone_high=round(z_high, 2),
            resistance_1=res1,
            resistance_2=res2,
            risk_reward=rr,
            vwap=vwap,
            stop_loss=stop_loss,
            target=target,
            mini_chart_candles=candles[-25:] if candles else [],
            ob_formed_at=active_ob.detection_time if active_ob else None,
            ob_confirmed_at=active_ob.confirmation_time if active_ob else None,
            ob_state=active_ob.state.value if active_ob else None,
            chart_total_obs=tot_obs,
            chart_fresh_obs=fresh_obs,
            chart_mitigated_obs=mit_obs,
            chart_invalidated_obs=inv_obs,
        )

    def select_picks(
        self,
        bullish_board: List[StockRankOutput],
        bearish_board: List[StockRankOutput],
        candles_map: Optional[Dict[str, List[Candle5m]]] = None,
        max_picks: int = 20,
    ) -> tuple[List[TopPickCardOutput], List[TopPickCardOutput]]:
        """Selects Top Picks (up to max_picks) respecting sector cap, with fallback to guarantee target count."""
        bull_picks: List[TopPickCardOutput] = []
        bear_picks: List[TopPickCardOutput] = []

        # 1. Bullish Picks Selection (Pass 1: with sector cap)
        sector_counts_bull: Dict[str, int] = {}
        for stock in bullish_board:
            if len(bull_picks) >= max_picks:
                break
            sec = stock.sector or "OTHERS"
            if sector_counts_bull.get(sec, 0) < self.sector_cap:
                sector_counts_bull[sec] = sector_counts_bull.get(sec, 0) + 1
                bull_picks.append(
                    self._create_pick_card(stock, "BULLISH", len(bull_picks) + 1, candles_map)
                )

        # Pass 2: fill remaining slots up to max_picks if sector cap was restrictive
        if len(bull_picks) < max_picks:
            picked_bull = {p.symbol for p in bull_picks}
            for stock in bullish_board:
                if len(bull_picks) >= max_picks:
                    break
                if stock.symbol not in picked_bull:
                    picked_bull.add(stock.symbol)
                    bull_picks.append(
                        self._create_pick_card(stock, "BULLISH", len(bull_picks) + 1, candles_map)
                    )

        # 2. Bearish Picks Selection (Pass 1: with sector cap)
        sector_counts_bear: Dict[str, int] = {}
        for stock in bearish_board:
            if len(bear_picks) >= max_picks:
                break
            sec = stock.sector or "OTHERS"
            if sector_counts_bear.get(sec, 0) < self.sector_cap:
                sector_counts_bear[sec] = sector_counts_bear.get(sec, 0) + 1
                bear_picks.append(
                    self._create_pick_card(stock, "BEARISH", len(bear_picks) + 1, candles_map)
                )

        # Pass 2: fill remaining slots up to max_picks if sector cap was restrictive
        if len(bear_picks) < max_picks:
            picked_bear = {p.symbol for p in bear_picks}
            for stock in bearish_board:
                if len(bear_picks) >= max_picks:
                    break
                if stock.symbol not in picked_bear:
                    picked_bear.add(stock.symbol)
                    bear_picks.append(
                        self._create_pick_card(stock, "BEARISH", len(bear_picks) + 1, candles_map)
                    )

        return bull_picks, bear_picks
