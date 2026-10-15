"""
OrderBlock Analysis: Market Breadth & Bee-Swarm Coordinate Mapper
File: backend/engines/breadth_engine.py
Maps all ~213 F&O stocks along a horizontal % move axis with 0% center.
Stacks advancing stocks vertically above zero and declining stocks below zero.
"""

from __future__ import annotations
from typing import List, Dict
from backend.core.schemas import BeeSwarmDot, StockRankOutput


class MarketBreadthEngine:
    """Calculates market advance/decline breadth and maps Bee-Swarm coordinates."""

    @staticmethod
    def compute_bee_swarm(
        dots: List[BeeSwarmDot],
        bucket_size_pct: float = 0.25
    ) -> List[BeeSwarmDot]:
        """Arranges dots into stacked vertical columns based on their % move buckets."""
        # Bucketing algorithm: group stocks by % move rounded to nearest bucket_size_pct
        buckets_pos: Dict[float, int] = {}
        buckets_neg: Dict[float, int] = {}
        packed_dots: List[BeeSwarmDot] = []

        for d in sorted(dots, key=lambda x: abs(x.change_pct)):
            bucket = round(round(d.change_pct / bucket_size_pct) * bucket_size_pct, 2)

            if d.change_pct >= 0:
                count = buckets_pos.get(bucket, 0)
                stack_y = (count + 1) * 6.0  # 6px spacing between dots vertically
                buckets_pos[bucket] = count + 1
            else:
                count = buckets_neg.get(bucket, 0)
                stack_y = -(count + 1) * 6.0
                buckets_neg[bucket] = count + 1

            packed_dots.append(BeeSwarmDot(
                symbol=d.symbol,
                change_pct=d.change_pct,
                stack_y=stack_y,
                is_pick=d.is_pick,
                event_risk=d.event_risk,
                board=d.board
            ))

        return packed_dots
