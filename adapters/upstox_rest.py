"""
Upstox REST API Adapter for Market Data & Historical Baselines
File: backend/adapters/upstox_rest.py

Handles:
- Fetching and caching the 213 NSE Equity F&O Underlying Universe
- Fetching D_50 completed daily bars for baseline engines (ATR, ADM, AHR, ADE)
- Fetching 1m / 5m intraday candle structures
- Live / Snapshot batch quotes with full market depth (Bid/Ask)
"""

from __future__ import annotations
import urllib.request
import urllib.error
import gzip
import io
import csv
import json
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

from backend.config import UPSTOX_ACCESS_TOKEN
from backend.core.schemas import DailyBar, IntradaySnapshot, Candle5m
from backend.core.constants import DEFAULT_BASELINE_LOOKBACK

UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"

DEFAULT_HEADERS = {
    "Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
}


class UpstoxRestAdapter:
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or UPSTOX_ACCESS_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        }
        # In-memory caches
        self._universe_cache: Dict[str, Dict[str, str]] = {}  # symbol -> {instrument_key, symbol, name}
        self._key_to_symbol: Dict[str, str] = {}
        self._daily_bars_cache: Dict[str, List[DailyBar]] = {}
        self._intraday_cache: Dict[str, Tuple[float, List[Candle5m]]] = {}

        # Thread-safe rate limiter (token bucket / minimum spacing)
        # Upstox API allows up to 10-15 req/sec. We enforce min 120ms between calls (~8.3 req/s max)
        self._rate_lock = threading.Lock()
        self._universe_lock = threading.Lock()
        self._daily_bars_lock = threading.Lock()
        self._intraday_lock = threading.Lock()
        self._last_req_time: float = 0.0
        self._min_interval: float = 0.12

    def _safe_get_json(self, url: str, timeout: int = 10, max_retries: int = 2) -> Optional[dict]:
        """
        Executes a GET request against Upstox REST API with rate limit spacing
        and exponential backoff retry on HTTP 429.
        """
        req = urllib.request.Request(url, headers=self.headers)
        for attempt in range(max_retries + 1):
            with self._rate_lock:
                now = time.time()
                elapsed = now - self._last_req_time
                if elapsed < self._min_interval:
                    time.sleep(self._min_interval - elapsed)
                self._last_req_time = time.time()

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    backoff = (attempt + 1) * 1.5
                    time.sleep(backoff)
                    continue
                return None
            except Exception:
                if attempt < max_retries:
                    time.sleep(0.5)
                    continue
                return None
        return None

    def get_fo_universe(self, force_refresh: bool = False) -> Dict[str, Dict[str, str]]:
        """
        Downloads and parses Upstox NSE instrument master, extracting all 213
        active NSE F&O underlying equities.
        """
        if self._universe_cache and not force_refresh:
            return self._universe_cache

        with self._universe_lock:
            if self._universe_cache and not force_refresh:
                return self._universe_cache

            req = urllib.request.Request(UPSTOX_INSTRUMENTS_URL, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content = gzip.decompress(resp.read()).decode("utf-8")
                    reader = list(csv.DictReader(io.StringIO(content)))
                    
                    fut_stks = [r for r in reader if r.get("instrument_type") == "FUTSTK"]
                    eq_stks = {r["name"]: r for r in reader if r.get("instrument_type") == "EQUITY"}

                    universe = {}
                    key_map = {}
                    for f in fut_stks:
                        name = f["name"]
                        if name in eq_stks:
                            eq = eq_stks[name]
                            sym = eq["tradingsymbol"]
                            ikey = eq["instrument_key"]
                            universe[sym] = {
                                "instrument_key": ikey,
                                "symbol": sym,
                                "name": name,
                            }
                            key_map[ikey] = sym

                    self._universe_cache = universe
                    self._key_to_symbol = key_map
                    return self._universe_cache
            except Exception as e:
                # Fallback to key bluechips if network glitch
                if not self._universe_cache:
                    fallback = {
                        "RELIANCE": {"instrument_key": "NSE_EQ|INE002A01018", "symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES"},
                        "HDFCBANK": {"instrument_key": "NSE_EQ|INE040A01034", "symbol": "HDFCBANK", "name": "HDFC BANK"},
                        "INFY": {"instrument_key": "NSE_EQ|INE009A01021", "symbol": "INFY", "name": "INFOSYS"},
                        "ICICIBANK": {"instrument_key": "NSE_EQ|INE090A01021", "symbol": "ICICIBANK", "name": "ICICI BANK"},
                        "TCS": {"instrument_key": "NSE_EQ|INE467B01029", "symbol": "TCS", "name": "TATA CONSULTANCY SERVICES"},
                        "SBIN": {"instrument_key": "NSE_EQ|INE062A01020", "symbol": "SBIN", "name": "STATE BANK OF INDIA"},
                        "EICHERMOT": {"instrument_key": "NSE_EQ|INE066A01021", "symbol": "EICHERMOT", "name": "EICHER MOTORS"},
                    }
                    self._universe_cache = fallback
                    for s, d in fallback.items():
                        self._key_to_symbol[d["instrument_key"]] = s
                return self._universe_cache

    def fetch_quotes_batch(self, instrument_keys: List[str]) -> Dict[str, Any]:
        """
        Fetches live market quotes for up to 500 instrument keys in a single request.
        Deduplicates keys to prevent redundant payload queries.
        """
        if not instrument_keys:
            return {}

        unique_keys = list(dict.fromkeys(instrument_keys))
        joined_keys = ",".join(unique_keys)
        url = f"{UPSTOX_BASE_URL}/market-quote/quotes?instrument_key={urllib.parse.quote(joined_keys)}"
        data = self._safe_get_json(url, timeout=10)
        return (data or {}).get("data", {})

    def fetch_daily_bars(
        self, instrument_key: str, symbol: str, lookback_days: int = DEFAULT_BASELINE_LOOKBACK
    ) -> List[DailyBar]:
        """
        Fetches completed daily bars for baseline engines.
        Zero future leakage: excludes today's uncompleted session.
        Thread-safe to prevent duplicate parallel fetches for the same symbol.
        """
        if symbol in self._daily_bars_cache:
            return self._daily_bars_cache[symbol]

        with self._daily_bars_lock:
            if symbol in self._daily_bars_cache:
                return self._daily_bars_cache[symbol]

            # 1. Check persistent SQLite store cache for instant zero-latency loading
            try:
                from backend.storage.sqlite_store import sqlite_store
                cached_bars = sqlite_store.get_daily_bars(symbol, lookback_days)
                if len(cached_bars) >= 40:
                    self._daily_bars_cache[symbol] = cached_bars
                    return cached_bars
            except Exception:
                pass

            encoded_key = urllib.parse.quote(instrument_key)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            from_str = (datetime.now(timezone.utc) - timedelta(days=130)).strftime("%Y-%m-%d")
            url = f"{UPSTOX_BASE_URL}/historical-candle/{encoded_key}/day/{today_str}/{from_str}"

            data = self._safe_get_json(url, timeout=10)
            raw_candles = (data or {}).get("data", {}).get("candles", [])
            if not raw_candles:
                self._daily_bars_cache[symbol] = []
                return []

            # Upstox returns newest first: [timestamp, open, high, low, close, volume, oi]
            bars: List[DailyBar] = []
            for c in reversed(raw_candles):
                ts_str = c[0]
                date_part = ts_str.split("T")[0]
                if date_part == today_str:
                    continue
                prev_c = bars[-1].close if bars else None
                bars.append(DailyBar(
                    trading_date=date_part,
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    prev_close=prev_c
                ))

            bars = bars[-lookback_days:]
            self._daily_bars_cache[symbol] = bars
            try:
                from backend.storage.sqlite_store import sqlite_store
                sqlite_store.save_daily_bars(symbol, bars)
            except Exception:
                pass
            return bars

    def fetch_intraday_candles_5m(self, instrument_key: str, symbol: str) -> List[Candle5m]:
        """
        Fetches 1m intraday candles from Upstox and aggregates them into completed 5m OHLCV bars.
        Includes a 90-second in-memory cache to prevent rate-limit flooding.
        Protected with lock to prevent duplicate concurrent queries for the same symbol.
        """
        now = time.time()
        if symbol in self._intraday_cache:
            ts, cached = self._intraday_cache[symbol]
            if (now - ts) < 90.0 and cached:
                return cached

        with self._intraday_lock:
            now = time.time()
            if symbol in self._intraday_cache:
                ts, cached = self._intraday_cache[symbol]
                if (now - ts) < 90.0 and cached:
                    return cached

            encoded_key = urllib.parse.quote(instrument_key)
            url = f"{UPSTOX_BASE_URL}/historical-candle/intraday/{encoded_key}/1minute"
            data = self._safe_get_json(url, timeout=10)
            raw_candles = (data or {}).get("data", {}).get("candles", [])

        if not raw_candles:
            # Off-market hours / weekend fallback: fetch recent 1m historical candles
            today_dt = datetime.now(timezone.utc)
            to_date = today_dt.strftime("%Y-%m-%d")
            from_date = (today_dt - timedelta(days=5)).strftime("%Y-%m-%d")
            hist_url = f"{UPSTOX_BASE_URL}/historical-candle/{encoded_key}/1minute/{to_date}/{from_date}"
            hist_data = self._safe_get_json(hist_url, timeout=10)
            raw_candles = (hist_data or {}).get("data", {}).get("candles", [])

        if not raw_candles:
            self._intraday_cache[symbol] = (now, [])
            return []

        # Convert to chronological order (oldest first)
        raw_candles.reverse()

        # Aggregate into 5-minute buckets
        five_min_bars: List[Candle5m] = []
        bucket: List[Any] = []
        current_bucket_key: Optional[str] = None

        for c in raw_candles:
            ts_str = c[0]
            dt = datetime.fromisoformat(ts_str)
            minute_slot = (dt.minute // 5) * 5
            dt_bucket = dt.replace(minute=minute_slot, second=0, microsecond=0)
            bucket_key = dt_bucket.isoformat()

            if current_bucket_key is None:
                current_bucket_key = bucket_key

            if bucket_key != current_bucket_key:
                if bucket:
                    five_min_bars.append(Candle5m(
                        timestamp=current_bucket_key,
                        open=float(bucket[0][1]),
                        high=max(float(x[2]) for x in bucket),
                        low=min(float(x[3]) for x in bucket),
                        close=float(bucket[-1][4]),
                        volume=sum(float(x[5]) for x in bucket)
                    ))
                bucket = [c]
                current_bucket_key = bucket_key
            else:
                bucket.append(c)

        # Flush final bar
        if bucket and current_bucket_key:
            five_min_bars.append(Candle5m(
                timestamp=current_bucket_key,
                open=float(bucket[0][1]),
                high=max(float(x[2]) for x in bucket),
                low=min(float(x[3]) for x in bucket),
                close=float(bucket[-1][4]),
                volume=sum(float(x[5]) for x in bucket)
            ))

        self._intraday_cache[symbol] = (now, five_min_bars)
        return five_min_bars
