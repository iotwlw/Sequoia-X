"""数据引擎属性测试。"""

import sqlite3
import tempfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

import sequoia_x.data.engine as engine_module
from sequoia_x.core.config import Settings
from sequoia_x.data.baostock_guard import BaostockRequestLimitError
from sequoia_x.data.engine import DataEngine


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    return engine, settings


# Property 4: (symbol, date) 唯一约束防止重复写入
@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，数据库中该组合记录数应保持为 1。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        row = {
            "symbol": symbol,
            "date": str(trade_date),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
            "turnover": 10500.0,
        }
        df = pd.DataFrame([row])
        with closing(sqlite3.connect(engine.db_path)) as conn:
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            try:
                df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            except sqlite3.IntegrityError:
                pass
            count = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND date=?",
                (symbol, str(trade_date)),
            ).fetchone()[0]
        assert count == 1


def test_sync_today_bulk_fetches_incrementally_in_one_batch(monkeypatch) -> None:
    """sync_today_bulk 应顺序抓取完整任务列表，不再拆分成多进程批次。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = date.today().strftime("%Y-%m-%d")
        seed = pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "date": yesterday,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000.0,
                    "turnover": 10500.0,
                },
                {
                    "symbol": "600000",
                    "date": yesterday,
                    "open": 20.0,
                    "high": 21.0,
                    "low": 19.0,
                    "close": 20.5,
                    "volume": 2000.0,
                    "turnover": 41000.0,
                },
            ]
        )
        with closing(sqlite3.connect(engine.db_path)) as conn:
            seed.to_sql(
                "stock_daily",
                conn,
                if_exists="append",
                index=False,
                method="multi",
            )

        captured_batches: list[list[tuple[str, str, str, str]]] = []

        def fake_fetch_batch(
            tasks: list[tuple[str, str, str, str]],
            state_dir: str | None = None,
        ) -> list[list[str]]:
            assert state_dir == engine.state_dir
            captured_batches.append(tasks)
            return [
                ["000001", today, "10", "11", "9", "10.8", "1200", "12960"],
                ["600000", today, "20", "21", "19", "20.8", "2200", "45760"],
            ]

        monkeypatch.setattr(engine_module, "_bs_fetch_batch", fake_fetch_batch)

        assert engine.sync_today_bulk() == 2
        assert len(captured_batches) == 1
        assert [task[0] for task in captured_batches[0]] == ["000001", "600000"]


def test_sync_today_bulk_preserves_symbols_already_current(monkeypatch, tmp_path) -> None:
    """补同一交易日的缺失股票时，不能删除其他已是最新的股票数据。"""
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    today = date.today().strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    seed = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "date": today,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "turnover": 10500.0,
            },
            {
                "symbol": "600000",
                "date": yesterday,
                "open": 20.0,
                "high": 21.0,
                "low": 19.0,
                "close": 20.5,
                "volume": 2000.0,
                "turnover": 41000.0,
            },
        ]
    )
    with closing(sqlite3.connect(engine.db_path)) as conn:
        seed.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")

    def fake_fetch_batch(tasks, state_dir=None):
        assert state_dir == engine.state_dir
        assert [task[0] for task in tasks] == ["600000"]
        return [["600000", today, "20", "21", "19", "20.8", "2200", "45760"]]

    monkeypatch.setattr(engine_module, "_bs_fetch_batch", fake_fetch_batch)

    assert engine.sync_today_bulk() == 1
    with closing(sqlite3.connect(engine.db_path)) as conn:
        rows = conn.execute(
            "SELECT symbol, close FROM stock_daily WHERE date = ? ORDER BY symbol",
            (today,),
        ).fetchall()

    assert rows == [("000001", 10.5), ("600000", 20.8)]


def test_backfill_stops_immediately_when_query_limit_is_reached(monkeypatch, tmp_path) -> None:
    """配额耗尽属于安全边界，不能按瞬时网络错误重试。"""
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    query_calls = 0

    monkeypatch.setattr(engine_module, "login_baostock", lambda logger: None)
    monkeypatch.setattr(engine_module, "logout_baostock", lambda logger: None)

    def raise_limit(*args, **kwargs):
        nonlocal query_calls
        query_calls += 1
        raise BaostockRequestLimitError("limit")

    monkeypatch.setattr(engine_module, "query_history_k_data_plus", raise_limit)

    with pytest.raises(BaostockRequestLimitError):
        engine.backfill(["600000"])

    assert query_calls == 1
