"""
OrderBlock Quantitative Engine: Unified Persistence Store
File: backend/storage/sqlite_store.py

Supports both SQLite (WAL mode) and Cloud MySQL (e.g. Aiven MySQL) with automatic
schema migration and resilient fallback.
Adheres strictly to docs/BE_rulebook.md.
"""

from __future__ import annotations
import json
import logging
import os
import sqlite3
import ssl
import threading
import time
import urllib.parse
from contextlib import contextmanager
from typing import List, Dict, Optional, Any

logger = logging.getLogger("orderblock.storage")


class StorageEngine:
    """Unified storage engine supporting SQLite and MySQL (Aiven) with graceful fallback."""

    def __init__(self, db_url: Optional[str] = None):
        from backend.config import DATABASE_URL
        self.raw_url = db_url or DATABASE_URL
        self.sqlite_path = "./backend/data/orderblock.db"
        self.engine_type = "sqlite"
        self.mysql_params: Dict[str, Any] = {}
        self._mysql_semaphore = threading.BoundedSemaphore(8)

        if self.raw_url and (self.raw_url.startswith("mysql://") or self.raw_url.startswith("mysql+pymysql://")):
            try:
                import pymysql  # type: ignore[import-untyped]
                parsed = urllib.parse.urlparse(self.raw_url)
                
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

                self.mysql_params = {
                    "host": parsed.hostname,
                    "port": parsed.port or 3306,
                    "user": parsed.username or "root",
                    "password": parsed.password or "",
                    "database": parsed.path.lstrip("/"),
                    "ssl": ssl_ctx,
                    "connect_timeout": 5,
                    "charset": "utf8mb4",
                    "cursorclass": pymysql.cursors.DictCursor
                }

                # Test connection
                with pymysql.connect(**self.mysql_params) as test_conn:
                    with test_conn.cursor() as cur:
                        cur.execute("SELECT 1;")
                
                self.engine_type = "mysql"
                logger.info(f"Connected to Cloud MySQL at {parsed.hostname}")
            except Exception as e:
                logger.warning(
                    f"Unable to connect to Cloud MySQL ({e}). "
                    "Gracefully falling back to high-performance local SQLite WAL mode."
                )
                self.engine_type = "sqlite"

        if self.engine_type == "sqlite":
            os.makedirs(os.path.dirname(os.path.abspath(self.sqlite_path)), exist_ok=True)

        self._init_db()

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _get_mysql_conn(self):
        import pymysql  # type: ignore[import-untyped]
        with self._mysql_semaphore:
            conn = pymysql.connect(**self.mysql_params)
            try:
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _init_db(self):
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS cycles (
                                timestamp VARCHAR(64) PRIMARY KEY,
                                universe_size INT,
                                valid_count INT,
                                quality VARCHAR(32),
                                published TINYINT,
                                payload_json LONGTEXT
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS order_block_zones (
                                zone_id VARCHAR(128) PRIMARY KEY,
                                symbol VARCHAR(32),
                                direction VARCHAR(16),
                                detection_time VARCHAR(64),
                                confirmation_time VARCHAR(64),
                                zone_high DOUBLE,
                                zone_low DOUBLE,
                                invalidation_level DOUBLE,
                                state VARCHAR(32),
                                touch_count INT,
                                invalidation_reason TEXT
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS candles_5m (
                                symbol VARCHAR(32),
                                timestamp VARCHAR(64),
                                open DOUBLE,
                                high DOUBLE,
                                low DOUBLE,
                                close DOUBLE,
                                volume DOUBLE,
                                PRIMARY KEY (symbol, timestamp)
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS orders (
                                order_id VARCHAR(128) PRIMARY KEY,
                                symbol VARCHAR(32),
                                side VARCHAR(16),
                                order_type VARCHAR(16),
                                quantity INT,
                                price DOUBLE,
                                stop_loss DOUBLE,
                                target DOUBLE,
                                status VARCHAR(32),
                                fill_price DOUBLE,
                                created_at VARCHAR(64),
                                filled_at VARCHAR(64),
                                metadata_json LONGTEXT
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS positions (
                                position_id VARCHAR(128) PRIMARY KEY,
                                symbol VARCHAR(32),
                                side VARCHAR(16),
                                quantity INT,
                                entry_price DOUBLE,
                                current_price DOUBLE,
                                unrealized_pnl DOUBLE,
                                realized_pnl DOUBLE,
                                stop_loss DOUBLE,
                                target DOUBLE,
                                status VARCHAR(32),
                                opened_at VARCHAR(64),
                                closed_at VARCHAR(64)
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS historical_daily_bars (
                                symbol VARCHAR(32),
                                trading_date VARCHAR(32),
                                open DOUBLE,
                                high DOUBLE,
                                low DOUBLE,
                                close DOUBLE,
                                PRIMARY KEY (symbol, trading_date)
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS trade_analytics (
                                trade_id VARCHAR(128) PRIMARY KEY,
                                position_id VARCHAR(128),
                                order_id VARCHAR(128),
                                symbol VARCHAR(32) NOT NULL,
                                side VARCHAR(16) NOT NULL,
                                entry_time VARCHAR(64) NOT NULL,
                                entry_price DOUBLE NOT NULL,
                                quantity INT NOT NULL,
                                target_price DOUBLE NOT NULL,
                                stop_loss_price DOUBLE NOT NULL,
                                exit_time VARCHAR(64) NOT NULL,
                                exit_price DOUBLE NOT NULL,
                                exit_reason VARCHAR(32) NOT NULL,
                                hit_type VARCHAR(32) NOT NULL,
                                realized_pnl DOUBLE NOT NULL,
                                return_pct DOUBLE NOT NULL,
                                r_multiple_achieved DOUBLE NOT NULL,
                                mfe DOUBLE,
                                mae DOUBLE,
                                holding_seconds INT,
                                chart_total_obs INT NOT NULL DEFAULT 0,
                                chart_fresh_obs INT NOT NULL DEFAULT 0,
                                chart_mitigated_obs INT NOT NULL DEFAULT 0,
                                chart_invalidated_obs INT NOT NULL DEFAULT 0,
                                active_ob_id VARCHAR(128),
                                active_ob_formed_at VARCHAR(64),
                                active_ob_confirmed_at VARCHAR(64),
                                active_ob_state VARCHAR(32),
                                metadata_json LONGTEXT
                            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """)
                    conn.commit()
            except Exception as e:
                logger.warning(f"MySQL schema initialization failed: {e}. Falling back to SQLite.")
                self.engine_type = "sqlite"

        # Always initialize SQLite initialization for local cache & resilient offline fallback
        os.makedirs(os.path.dirname(os.path.abspath(self.sqlite_path)), exist_ok=True)
        with self._get_sqlite_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cycles (
                    timestamp TEXT PRIMARY KEY,
                    universe_size INTEGER,
                    valid_count INTEGER,
                    quality TEXT,
                    published INTEGER,
                    payload_json TEXT
                );
                CREATE TABLE IF NOT EXISTS order_block_zones (
                    zone_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    direction TEXT,
                    detection_time TEXT,
                    confirmation_time TEXT,
                    zone_high REAL,
                    zone_low REAL,
                    invalidation_level REAL,
                    state TEXT,
                    touch_count INTEGER,
                    invalidation_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS candles_5m (
                    symbol TEXT,
                    timestamp TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY (symbol, timestamp)
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    order_type TEXT,
                    quantity INTEGER,
                    price REAL,
                    stop_loss REAL,
                    target REAL,
                    status TEXT,
                    fill_price REAL,
                    created_at TEXT,
                    filled_at TEXT,
                    metadata_json TEXT
                );
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    quantity INTEGER,
                    entry_price REAL,
                    current_price REAL,
                    unrealized_pnl REAL,
                    realized_pnl REAL,
                    stop_loss REAL,
                    target REAL,
                    status TEXT,
                    opened_at TEXT,
                    closed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS historical_daily_bars (
                    symbol TEXT,
                    trading_date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    PRIMARY KEY (symbol, trading_date)
                );
                CREATE TABLE IF NOT EXISTS trade_analytics (
                    trade_id TEXT PRIMARY KEY,
                    position_id TEXT,
                    order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    target_price REAL NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    exit_time TEXT NOT NULL,
                    exit_price REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    hit_type TEXT NOT NULL,
                    realized_pnl REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    r_multiple_achieved REAL NOT NULL,
                    mfe REAL,
                    mae REAL,
                    holding_seconds INTEGER,
                    chart_total_obs INTEGER NOT NULL DEFAULT 0,
                    chart_fresh_obs INTEGER NOT NULL DEFAULT 0,
                    chart_mitigated_obs INTEGER NOT NULL DEFAULT 0,
                    chart_invalidated_obs INTEGER NOT NULL DEFAULT 0,
                    active_ob_id TEXT,
                    active_ob_formed_at TEXT,
                    active_ob_confirmed_at TEXT,
                    active_ob_state TEXT,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ob_symbol ON order_block_zones(symbol);
                CREATE INDEX IF NOT EXISTS idx_hist_sym ON historical_daily_bars(symbol);
                CREATE INDEX IF NOT EXISTS idx_candles_sym_ts ON candles_5m(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
                CREATE INDEX IF NOT EXISTS idx_positions_sym_status ON positions(symbol, status);
                CREATE INDEX IF NOT EXISTS idx_ta_sym ON trade_analytics(symbol);
                CREATE INDEX IF NOT EXISTS idx_ta_hit_type ON trade_analytics(hit_type);
                CREATE INDEX IF NOT EXISTS idx_ta_exit_time ON trade_analytics(exit_time);
            """)

    def save_cycle_snapshot(self, timestamp: str, universe_size: int, valid_count: int, quality: str, published: bool, payload: Dict[str, Any]):
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """REPLACE INTO cycles (timestamp, universe_size, valid_count, quality, published, payload_json)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (timestamp, universe_size, valid_count, quality, 1 if published else 0, json.dumps(payload))
                        )
                    conn.commit()
                return
            except Exception as e:
                logger.warning(f"Failed to save cycle to MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cycles (timestamp, universe_size, valid_count, quality, published, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (timestamp, universe_size, valid_count, quality, 1 if published else 0, json.dumps(payload))
            )

    def save_order_block_zone(self, zone: Dict[str, Any]):
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """REPLACE INTO order_block_zones 
                               (zone_id, symbol, direction, detection_time, confirmation_time, zone_high, zone_low, invalidation_level, state, touch_count, invalidation_reason)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (
                                zone["zone_id"], zone["symbol"], zone["direction"], zone["detection_time"],
                                zone["confirmation_time"], zone["zone_high"], zone["zone_low"],
                                zone["invalidation_level"], zone["state"], zone["touch_count"],
                                zone.get("invalidation_reason")
                            )
                        )
                    conn.commit()
                return
            except Exception as e:
                logger.warning(f"Failed to save zone to MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO order_block_zones 
                   (zone_id, symbol, direction, detection_time, confirmation_time, zone_high, zone_low, invalidation_level, state, touch_count, invalidation_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    zone["zone_id"], zone["symbol"], zone["direction"], zone["detection_time"],
                    zone["confirmation_time"], zone["zone_high"], zone["zone_low"],
                    zone["invalidation_level"], zone["state"], zone["touch_count"],
                    zone.get("invalidation_reason")
                )
            )

    def get_active_zones(self, symbol: str) -> List[Dict[str, Any]]:
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT * FROM order_block_zones WHERE symbol = %s AND state != 'INVALIDATED'",
                            (symbol,)
                        )
                        return list(cur.fetchall())
            except Exception as e:
                logger.warning(f"Failed to get active zones from MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM order_block_zones WHERE symbol = ? AND state != 'INVALIDATED'",
                (symbol,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def save_candle(self, symbol: str, timestamp: str, open_: float, high: float, low: float, close: float, volume: float = 0.0):
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """REPLACE INTO candles_5m (symbol, timestamp, open, high, low, close, volume)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (symbol, timestamp, open_, high, low, close, volume)
                        )
                    conn.commit()
                return
            except Exception as e:
                logger.warning(f"Failed to save candle to MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO candles_5m (symbol, timestamp, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol, timestamp, open_, high, low, close, volume)
            )

    def get_candles(self, symbol: str, limit: int = 75) -> List[Dict[str, Any]]:
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT * FROM candles_5m WHERE symbol = %s ORDER BY timestamp DESC LIMIT %s",
                            (symbol, limit)
                        )
                        rows = cur.fetchall()
                        return list(reversed(rows))
            except Exception as e:
                logger.warning(f"Failed to get candles from MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM candles_5m WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            )
            rows = cursor.fetchall()
            return [dict(r) for r in reversed(rows)]

    def save_order(self, order_dict: Dict[str, Any]):
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """REPLACE INTO orders 
                               (order_id, symbol, side, order_type, quantity, price, stop_loss, target, status, fill_price, created_at, filled_at, metadata_json)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (
                                order_dict["order_id"], order_dict["symbol"], order_dict["side"],
                                order_dict.get("order_type", "MARKET"), order_dict["quantity"],
                                order_dict.get("price", 0.0), order_dict.get("stop_loss", 0.0),
                                order_dict.get("target", 0.0), order_dict.get("status", "PENDING"),
                                order_dict.get("fill_price", 0.0), order_dict["created_at"],
                                order_dict.get("filled_at"), json.dumps(order_dict.get("metadata", {}))
                            )
                        )
                    conn.commit()
                return
            except Exception as e:
                logger.warning(f"Failed to save order to MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO orders 
                   (order_id, symbol, side, order_type, quantity, price, stop_loss, target, status, fill_price, created_at, filled_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_dict["order_id"], order_dict["symbol"], order_dict["side"],
                    order_dict.get("order_type", "MARKET"), order_dict["quantity"],
                    order_dict.get("price", 0.0), order_dict.get("stop_loss", 0.0),
                    order_dict.get("target", 0.0), order_dict.get("status", "PENDING"),
                    order_dict.get("fill_price", 0.0), order_dict["created_at"],
                    order_dict.get("filled_at"), json.dumps(order_dict.get("metadata", {}))
                )
            )

    def get_orders(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        if status:
                            cur.execute("SELECT * FROM orders WHERE status = %s ORDER BY created_at DESC", (status,))
                        else:
                            cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
                        return list(cur.fetchall())
            except Exception as e:
                logger.warning(f"Failed to get orders from MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            if status:
                cursor = conn.execute("SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC", (status,))
            else:
                cursor = conn.execute("SELECT * FROM orders ORDER BY created_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def save_position(self, pos_dict: Dict[str, Any]):
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """REPLACE INTO positions
                               (position_id, symbol, side, quantity, entry_price, current_price, unrealized_pnl, realized_pnl, stop_loss, target, status, opened_at, closed_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (
                                pos_dict["position_id"], pos_dict["symbol"], pos_dict["side"],
                                pos_dict["quantity"], pos_dict["entry_price"], pos_dict.get("current_price", pos_dict["entry_price"]),
                                pos_dict.get("unrealized_pnl", 0.0), pos_dict.get("realized_pnl", 0.0),
                                pos_dict.get("stop_loss", 0.0), pos_dict.get("target", 0.0),
                                pos_dict.get("status", "OPEN"), pos_dict["opened_at"], pos_dict.get("closed_at")
                            )
                        )
                    conn.commit()
                return
            except Exception as e:
                logger.warning(f"Failed to save position to MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO positions
                   (position_id, symbol, side, quantity, entry_price, current_price, unrealized_pnl, realized_pnl, stop_loss, target, status, opened_at, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pos_dict["position_id"], pos_dict["symbol"], pos_dict["side"],
                    pos_dict["quantity"], pos_dict["entry_price"], pos_dict.get("current_price", pos_dict["entry_price"]),
                    pos_dict.get("unrealized_pnl", 0.0), pos_dict.get("realized_pnl", 0.0),
                    pos_dict.get("stop_loss", 0.0), pos_dict.get("target", 0.0),
                    pos_dict.get("status", "OPEN"), pos_dict["opened_at"], pos_dict.get("closed_at")
                )
            )

    def get_open_positions(self) -> List[Dict[str, Any]]:
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM positions WHERE status = 'OPEN'")
                        return list(cur.fetchall())
            except Exception as e:
                logger.warning(f"Failed to get open positions from MySQL: {e}")

        with self._get_sqlite_conn() as conn:
            cursor = conn.execute("SELECT * FROM positions WHERE status = 'OPEN'")
            return [dict(r) for r in cursor.fetchall()]

    def save_daily_bars(self, symbol: str, bars: List[Any]):
        """Persists historical daily bars for instant startup and baseline caching."""
        if not bars:
            return
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """REPLACE INTO historical_daily_bars (symbol, trading_date, open, high, low, close)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            [(symbol, getattr(b, "trading_date", ""), float(getattr(b, "open", 0.0)), float(getattr(b, "high", 0.0)), float(getattr(b, "low", 0.0)), float(getattr(b, "close", 0.0))) for b in bars]
                        )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Failed to save daily bars to MySQL: {e}")

        try:
            with self._get_sqlite_conn() as conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO historical_daily_bars (symbol, trading_date, open, high, low, close)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [(symbol, getattr(b, "trading_date", ""), float(getattr(b, "open", 0.0)), float(getattr(b, "high", 0.0)), float(getattr(b, "low", 0.0)), float(getattr(b, "close", 0.0))) for b in bars]
                )
        except Exception as e:
            logger.warning(f"Failed to save daily bars to SQLite: {e}")

    def get_daily_bars(self, symbol: str, lookback: int = 50) -> List[Any]:
        """Loads cached historical daily bars from persistent storage."""
        from backend.core.schemas import DailyBar
        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """SELECT trading_date, open, high, low, close 
                               FROM historical_daily_bars 
                               WHERE symbol = %s 
                               ORDER BY trading_date ASC""",
                            (symbol,)
                        )
                        rows = cur.fetchall()
                        if rows:
                            bars: List[DailyBar] = []
                            for r in rows[-lookback:]:
                                prev_c = bars[-1].close if bars else None
                                bars.append(DailyBar(
                                    trading_date=r["trading_date"],
                                    open=float(r["open"]),
                                    high=float(r["high"]),
                                    low=float(r["low"]),
                                    close=float(r["close"]),
                                    prev_close=prev_c
                                ))
                            return bars
            except Exception as e:
                logger.warning(f"Failed to get daily bars from MySQL: {e}")

        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.execute(
                    """SELECT trading_date, open, high, low, close 
                       FROM historical_daily_bars 
                       WHERE symbol = ? 
                       ORDER BY trading_date ASC""",
                    (symbol,)
                )
                rows = cur.fetchall()
                if not rows:
                    return []
                bars: List[DailyBar] = []
                for r in rows[-lookback:]:
                    prev_c = bars[-1].close if bars else None
                    bars.append(DailyBar(
                        trading_date=r[0],
                        open=float(r[1]),
                        high=float(r[2]),
                        low=float(r[3]),
                        close=float(r[4]),
                        prev_close=prev_c
                    ))
                return bars
        except Exception as e:
            logger.warning(f"Failed to get daily bars from SQLite: {e}")
            return []

    def save_trade_analytics(self, trade: Dict[str, Any]):
        """Persists comprehensive trade execution, target/SL hit, and OB chart metrics."""
        cols = (
            "trade_id", "position_id", "order_id", "symbol", "side",
            "entry_time", "entry_price", "quantity", "target_price", "stop_loss_price",
            "exit_time", "exit_price", "exit_reason", "hit_type",
            "realized_pnl", "return_pct", "r_multiple_achieved", "mfe", "mae", "holding_seconds",
            "chart_total_obs", "chart_fresh_obs", "chart_mitigated_obs", "chart_invalidated_obs",
            "active_ob_id", "active_ob_formed_at", "active_ob_confirmed_at", "active_ob_state", "metadata_json"
        )
        vals = [
            trade.get("trade_id", f"TRD-{int(time.time())}"),
            trade.get("position_id"),
            trade.get("order_id"),
            trade.get("symbol", "UNKNOWN"),
            trade.get("side", "LONG"),
            trade.get("entry_time", ""),
            float(trade.get("entry_price", 0.0)),
            int(trade.get("quantity", 0)),
            float(trade.get("target_price", 0.0)),
            float(trade.get("stop_loss_price", 0.0)),
            trade.get("exit_time", ""),
            float(trade.get("exit_price", 0.0)),
            trade.get("exit_reason", "MANUAL"),
            trade.get("hit_type", "MANUAL"),
            float(trade.get("realized_pnl", 0.0)),
            float(trade.get("return_pct", 0.0)),
            float(trade.get("r_multiple_achieved", 0.0)),
            float(trade.get("mfe", trade.get("exit_price", 0.0))),
            float(trade.get("mae", trade.get("entry_price", 0.0))),
            int(trade.get("holding_seconds", 0)),
            int(trade.get("chart_total_obs", 0)),
            int(trade.get("chart_fresh_obs", 0)),
            int(trade.get("chart_mitigated_obs", 0)),
            int(trade.get("chart_invalidated_obs", 0)),
            trade.get("active_ob_id"),
            trade.get("active_ob_formed_at"),
            trade.get("active_ob_confirmed_at"),
            trade.get("active_ob_state"),
            json.dumps(trade.get("metadata", {}))
        ]

        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        placeholders = ", ".join(["%s"] * len(vals))
                        col_str = ", ".join(cols)
                        cur.execute(f"REPLACE INTO trade_analytics ({col_str}) VALUES ({placeholders})", vals)
                    conn.commit()
            except Exception as e:
                logger.warning(f"Failed to save trade analytics to MySQL: {e}")

        try:
            with self._get_sqlite_conn() as conn:
                placeholders = ", ".join(["?"] * len(vals))
                col_str = ", ".join(cols)
                conn.execute(f"INSERT OR REPLACE INTO trade_analytics ({col_str}) VALUES ({placeholders})", vals)
        except Exception as e:
            logger.warning(f"Failed to save trade analytics to SQLite: {e}")

    def get_trade_analytics(
        self, symbol: Optional[str] = None, hit_type: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Queries historical trade analytics records with filtering."""
        query = "SELECT * FROM trade_analytics"
        clauses = []
        params: List[Any] = []
        if symbol:
            clauses.append("symbol = %s" if self.engine_type == "mysql" else "symbol = ?")
            params.append(symbol.upper())
        if hit_type:
            clauses.append("hit_type = %s" if self.engine_type == "mysql" else "hit_type = ?")
            params.append(hit_type.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY exit_time DESC LIMIT %s" if self.engine_type == "mysql" else " ORDER BY exit_time DESC LIMIT ?"
        params.append(limit)

        if self.engine_type == "mysql":
            try:
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                        rows = cur.fetchall()
                        for r in rows:
                            if isinstance(r.get("metadata_json"), str):
                                try:
                                    r["metadata"] = json.loads(r["metadata_json"])
                                except Exception:
                                    r["metadata"] = {}
                        return list(rows)
            except Exception as e:
                logger.warning(f"Failed to get trade analytics from MySQL: {e}")

        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.execute(query.replace("%s", "?"), params)
                records = []
                for row in cur.fetchall():
                    d = dict(row)
                    if d.get("metadata_json"):
                        try:
                            d["metadata"] = json.loads(d["metadata_json"])
                        except Exception:
                            d["metadata"] = {}
                    records.append(d)
                return records
        except Exception as e:
            logger.warning(f"Failed to get trade analytics from SQLite: {e}")
            return []

    def get_trade_summary(self) -> Dict[str, Any]:
        """Calculates win rate, target vs stop-loss hit stats, and OB density correlation."""
        trades = self.get_trade_analytics(limit=500)
        total = len(trades)
        if total == 0:
            return {
                "total_trades": 0,
                "target_hits": 0,
                "stop_loss_hits": 0,
                "manual_closes": 0,
                "win_rate_pct": 0.0,
                "total_realized_pnl": 0.0,
                "avg_r_multiple": 0.0,
                "avg_obs_winning_trades": 0.0,
                "avg_obs_losing_trades": 0.0,
            }

        target_hits = sum(1 for t in trades if t.get("hit_type") == "TARGET")
        sl_hits = sum(1 for t in trades if t.get("hit_type") == "STOP_LOSS")
        manual = total - (target_hits + sl_hits)
        total_pnl = sum(float(t.get("realized_pnl", 0.0)) for t in trades)
        avg_rr = sum(float(t.get("r_multiple_achieved", 0.0)) for t in trades) / total

        winning = [t for t in trades if float(t.get("realized_pnl", 0.0)) > 0]
        losing = [t for t in trades if float(t.get("realized_pnl", 0.0)) <= 0]
        avg_obs_win = (sum(int(t.get("chart_total_obs", 0)) for t in winning) / len(winning)) if winning else 0.0
        avg_obs_loss = (sum(int(t.get("chart_total_obs", 0)) for t in losing) / len(losing)) if losing else 0.0

        return {
            "total_trades": total,
            "target_hits": target_hits,
            "stop_loss_hits": sl_hits,
            "manual_closes": manual,
            "win_rate_pct": round((target_hits / total) * 100.0, 1) if total > 0 else 0.0,
            "total_realized_pnl": round(total_pnl, 2),
            "avg_r_multiple": round(avg_rr, 2),
            "avg_obs_winning_trades": round(avg_obs_win, 1),
            "avg_obs_losing_trades": round(avg_obs_loss, 1),
        }

    def get_all_traded_ob_ids(self) -> set:
        """Retrieves all historical order block IDs that have already been traded to prevent duplicates."""
        query = "SELECT DISTINCT active_ob_id FROM trade_analytics WHERE active_ob_id IS NOT NULL AND active_ob_id != '';"
        try:
            if self.engine_type == "mysql":
                with self._get_mysql_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(query)
                        rows = cur.fetchall()
                        return {r["active_ob_id"] for r in rows if r and r.get("active_ob_id")}
            else:
                with self._get_sqlite_conn() as conn:
                    cur = conn.execute(query)
                    rows = cur.fetchall()
                    return {r[0] for r in rows if r and r[0]}
        except Exception as e:
            logger.error(f"[Storage] Failed to fetch traded_ob_ids: {e}")
            return set()


# Export backward-compatible alias and singleton instance
SQLiteStorageEngine = StorageEngine
sqlite_store = StorageEngine()
