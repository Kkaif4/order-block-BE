"""
OrderBlock Quantitative Engine: Atomic NDJSON Cycle Ingestion Slice
File: backend/core/ingestion_slice.py
Zero external dependencies beyond Python standard library.
Enforces atomic write via temporary file + os.link/atomic rename, crash-resilience.
"""

from __future__ import annotations
import json
import os
import re
import tempfile
from dataclasses import dataclass, asdict
from typing import List, Optional

from backend.core.constants import SYMBOL_REGEX, RFC3339_REGEX


@dataclass(frozen=True)
class RawMarketQuote:
    """Raw market tick observation before cycle synchronization."""
    instrument_key: str
    symbol: str
    exchange_trade_timestamp_utc: str
    feed_dispatch_timestamp_utc: str
    local_receipt_timestamp_utc: str
    open: float
    high: float
    low: float
    close: float
    ltp: float
    prev_close: float
    volume: int = 0
    open_interest: float = 0.0

    def __post_init__(self):
        if not SYMBOL_REGEX.match(self.symbol):
            raise ValueError(f"Invalid symbol in RawMarketQuote: '{self.symbol}'")
        for ts_name, ts_val in [
            ("exchange_trade_timestamp_utc", self.exchange_trade_timestamp_utc),
            ("feed_dispatch_timestamp_utc", self.feed_dispatch_timestamp_utc),
            ("local_receipt_timestamp_utc", self.local_receipt_timestamp_utc)
        ]:
            if not isinstance(ts_val, str) or not RFC3339_REGEX.match(ts_val):
                raise TypeError(f"{ts_name} must be a strict RFC 3339 string. Got: {ts_val}")


class SnapshotStorageEngine:
    """Crash-resilient append-only NDJSON recorder with atomic publication."""

    def __init__(self, base_dir: str = "./backend/data/cycles"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def write_cycle(self, cycle_id: str, quotes: List[RawMarketQuote]) -> str:
        if not re.match(r"^[a-zA-Z0-9_\-]+$", cycle_id):
            raise ValueError(f"Invalid cycle_id: '{cycle_id}'")

        final_path = os.path.join(self.base_dir, f"{cycle_id}.jsonl")

        with tempfile.NamedTemporaryFile("w", dir=self.base_dir, delete=False, encoding="utf-8") as tmp:
            tmp_path = tmp.name
            for q in quotes:
                tmp.write(json.dumps(asdict(q)) + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())

        try:
            if hasattr(os, "link"):
                os.link(tmp_path, final_path)
                os.remove(tmp_path)
            else:
                if os.path.exists(final_path):
                    raise FileExistsError(f"Dataset for cycle_id '{cycle_id}' already exists.")
                os.rename(tmp_path, final_path)
        except FileExistsError:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise FileExistsError(f"Dataset for cycle_id '{cycle_id}' already exists at {final_path}.")
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        return final_path

    def read_cycle(self, filepath: str) -> List[RawMarketQuote]:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        quotes = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                raw = line.strip()
                if raw:
                    try:
                        d = json.loads(raw)
                        quotes.append(RawMarketQuote(**d))
                    except Exception as e:
                        raise ValueError(f"Corrupted record at line {line_no} in {filepath}: {e}")
        return quotes
