"""离线环境诊断：不访问行情服务，也不发送真实 Webhook。"""

import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from sequoia_x.core.config import Settings, is_placeholder_webhook
from sequoia_x.data.baostock_guard import BaostockGuardStateError, get_query_usage

DiagnosticStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """一条可测试、可展示的诊断结论。"""

    status: DiagnosticStatus
    name: str
    detail: str


def _webhook_diagnostic(settings: Settings) -> Diagnostic:
    webhook = settings.feishu_webhook_url.strip()
    placeholder_routes = [
        key for key, url in settings.strategy_webhooks.items() if is_placeholder_webhook(url)
    ]
    if is_placeholder_webhook(webhook):
        return Diagnostic(
            "warning",
            "飞书配置",
            "当前仍是示例 Webhook；真实运行前请在 .env 中填写 FEISHU_WEBHOOK_URL。",
        )
    if placeholder_routes:
        return Diagnostic(
            "warning",
            "飞书配置",
            "以下策略仍是示例 Webhook，将自动回退默认地址："
            + ", ".join(sorted(placeholder_routes)),
        )
    return Diagnostic(
        "ok",
        "飞书配置",
        f"默认 Webhook 已配置，另有 {len(settings.strategy_webhooks)} 个策略专属路由。",
    )


def _database_diagnostic(settings: Settings) -> Diagnostic:
    db_path = Path(settings.db_path).expanduser()
    if not db_path.exists():
        return Diagnostic(
            "warning",
            "行情数据库",
            f"尚未发现 {db_path}；下一步运行 uv run python main.py --backfill。",
        )

    try:
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stock_daily'"
            ).fetchone()
            if not table_exists:
                return Diagnostic(
                    "error",
                    "行情数据库",
                    f"{db_path} 缺少 stock_daily 表；请备份后重新初始化数据库。",
                )

            row_count, symbol_count, latest_date = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol), MAX(date) FROM stock_daily"
            ).fetchone()
    except sqlite3.Error as exc:
        return Diagnostic("error", "行情数据库", f"无法读取 {db_path}：{exc}")

    if row_count == 0:
        return Diagnostic(
            "warning",
            "行情数据库",
            f"{db_path} 已初始化但没有行情；下一步运行 uv run python main.py --backfill。",
        )

    try:
        stale_days = (date.today() - date.fromisoformat(latest_date)).days
    except (TypeError, ValueError):
        return Diagnostic("error", "行情数据库", f"数据库最新日期格式异常：{latest_date}")
    if stale_days > 7:
        return Diagnostic(
            "warning",
            "行情数据库",
            f"已收录 {symbol_count} 只股票、{row_count} 条日 K，但最新日期是 {latest_date}；"
            "建议运行 uv run python main.py 补齐增量数据。",
        )
    return Diagnostic(
        "ok",
        "行情数据库",
        f"已收录 {symbol_count} 只股票、{row_count} 条日 K，最新日期 {latest_date}。",
    )


def _quota_diagnostic(settings: Settings) -> Diagnostic:
    state_dir = Path(settings.state_dir).expanduser()
    try:
        usage_date, used, limit = get_query_usage(state_dir=state_dir)
    except BaostockGuardStateError as exc:
        return Diagnostic("error", "baostock 配额", str(exc))

    return Diagnostic(
        "ok",
        "baostock 配额",
        f"{usage_date} 已领取 {used}/{limit} 次；共享状态目录为 {state_dir}。",
    )


def collect_diagnostics(settings: Settings) -> list[Diagnostic]:
    """收集不触网的本地诊断结果。"""
    python_ok = sys.version_info >= (3, 10)
    results = [
        Diagnostic(
            "ok" if python_ok else "error",
            "Python 版本",
            f"当前 {sys.version.split()[0]}；项目要求 Python >= 3.10。",
        ),
        _webhook_diagnostic(settings),
        _database_diagnostic(settings),
        _quota_diagnostic(settings),
    ]
    return results


def run_doctor(settings: Settings, logger: Any) -> bool:
    """输出诊断结果；仅出现 error 时返回 False。"""
    labels = {"ok": "通过", "warning": "提醒", "error": "失败"}
    results = collect_diagnostics(settings)
    logger.info("Sequoia-X 离线诊断开始（不会访问 baostock，也不会发送飞书）")
    for result in results:
        message = f"[{labels[result.status]}] {result.name}：{result.detail}"
        if result.status == "error":
            logger.error(message)
        elif result.status == "warning":
            logger.warning(message)
        else:
            logger.info(message)
    logger.info("诊断完成：提醒项允许继续学习，失败项需要先修复。")
    return all(result.status != "error" for result in results)
