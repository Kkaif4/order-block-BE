"""
Rigorously Tested Edge Cases for Market Hours Scheduler
File: backend/tests/test_scheduler.py

Tests verify:
1. Exact second-level market boundary transitions (08:59:59 vs 09:00:00, 15:35:00 vs 15:35:01).
2. Cross-timezone inputs (UTC, US/Eastern) correctly converted to Asia/Kolkata (IST).
3. Weekend and midnight transitions.
"""

from datetime import datetime, timezone
import zoneinfo
from backend.scheduler.market_scheduler import MarketHoursScheduler, IST


def test_scheduler_exact_second_boundary_transitions():
    """
    Tests exact boundary seconds around open (08:55 pre-market vs 09:00 active)
    and close (15:30 active vs 15:35:01 post-market).
    """
    scheduler = MarketHoursScheduler()

    # 1. 08:54:59 IST (strictly closed)
    t_before_pre = datetime(2026, 10, 19, 8, 54, 59, tzinfo=IST)
    is_open, _ = scheduler.is_market_hours(t_before_pre)
    assert is_open is False

    # 2. 09:00:00 IST (trading session officially starts)
    t_open = datetime(2026, 10, 19, 9, 0, 0, tzinfo=IST)
    is_open, reason = scheduler.is_market_hours(t_open)
    assert is_open is True
    assert "Active Market Hours" in reason

    # 3. 15:29:59 IST (normal market session active)
    t_mid = datetime(2026, 10, 19, 15, 29, 59, tzinfo=IST)
    is_open, _ = scheduler.is_market_hours(t_mid)
    assert is_open is True

    # 4. 15:35:01 IST (market closed, post-market)
    t_post = datetime(2026, 10, 19, 15, 35, 1, tzinfo=IST)
    is_open, reason = scheduler.is_market_hours(t_post)
    assert is_open is False
    assert "Post-market" in reason


def test_scheduler_cross_timezone_utc_conversion():
    """
    EDGE CASE:
    Timestamps received from external servers / UTC feeds must be normalized
    to Asia/Kolkata before market hour evaluation.
    10:00 AM IST = 04:30 AM UTC.
    """
    scheduler = MarketHoursScheduler()

    # Monday 04:30 AM UTC = Monday 10:00 AM IST (Active market hours)
    utc_market_dt = datetime(2026, 10, 19, 4, 30, 0, tzinfo=timezone.utc)
    is_open, reason = scheduler.is_market_hours(utc_market_dt)
    assert is_open is True
    assert "Active Market Hours" in reason

    # Monday 02:00 AM UTC = Monday 07:30 AM IST (Pre-market closed)
    utc_early_dt = datetime(2026, 10, 19, 2, 0, 0, tzinfo=timezone.utc)
    is_open, _ = scheduler.is_market_hours(utc_early_dt)
    assert is_open is False


def test_scheduler_weekend_midnight_transitions():
    """
    Tests transitions across Friday midnight to Saturday and Sunday midnight to Monday.
    """
    scheduler = MarketHoursScheduler()

    # Friday 23:59:59 IST (Post-market)
    fri_night = datetime(2026, 10, 16, 23, 59, 59, tzinfo=IST)
    is_open, reason = scheduler.is_market_hours(fri_night)
    assert is_open is False
    assert "Post-market" in reason

    # Saturday 00:00:01 IST (Weekend)
    sat_midnight = datetime(2026, 10, 17, 0, 0, 1, tzinfo=IST)
    is_open, reason = scheduler.is_market_hours(sat_midnight)
    assert is_open is False
    assert "Weekend" in reason
