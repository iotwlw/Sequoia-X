"""离线诊断功能测试。"""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from sequoia_x.core.config import Settings
from sequoia_x.core.doctor import collect_diagnostics
from sequoia_x.data.engine import DataEngine


def make_settings(tmp_path: Path, webhook_url: str = "https://example.com/hook") -> Settings:
    return Settings(
        db_path=str(tmp_path / "market.db"),
        start_date="2024-01-01",
        feishu_webhook_url=webhook_url,
    )


def test_doctor_turns_missing_database_into_next_step(monkeypatch, tmp_path) -> None:
    """未回填时给出可执行下一步，而不是访问外部服务。"""
    monkeypatch.setenv("SEQUOIA_X_STATE_DIR", str(tmp_path / "state"))
    results = collect_diagnostics(make_settings(tmp_path))
    database = next(result for result in results if result.name == "行情数据库")

    assert database.status == "warning"
    assert "python main.py --backfill" in database.detail


def test_doctor_reports_local_database_summary(monkeypatch, tmp_path) -> None:
    """已有行情时报告股票数、记录数和最新日期。"""
    monkeypatch.setenv("SEQUOIA_X_STATE_DIR", str(tmp_path / "state"))
    settings = make_settings(tmp_path, webhook_url="https://open.feishu.cn/real-token")
    engine = DataEngine(settings)
    today = date.today().isoformat()
    with closing(sqlite3.connect(engine.db_path)) as conn:
        conn.execute(
            """
            INSERT INTO stock_daily
                (symbol, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("600000", today, 10, 11, 9, 10.5, 1000, 10500),
        )
        conn.commit()

    results = collect_diagnostics(settings)
    database = next(result for result in results if result.name == "行情数据库")

    assert database.status == "ok"
    assert "1 只股票" in database.detail
    assert "1 条日 K" in database.detail
    assert today in database.detail


def test_doctor_warns_about_placeholder_webhook(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SEQUOIA_X_STATE_DIR", str(tmp_path / "state"))
    results = collect_diagnostics(make_settings(tmp_path))
    webhook = next(result for result in results if result.name == "飞书配置")

    assert webhook.status == "warning"
    assert "FEISHU_WEBHOOK_URL" in webhook.detail
