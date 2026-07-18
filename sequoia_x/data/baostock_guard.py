"""baostock 访问守卫：原子请求配额、登录和查询异常分类。"""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from sequoia_x.core.runtime_state import get_runtime_state_dir

DAILY_QUERY_LIMIT = 50_000
BLACKLIST_ERROR_CODE = "10001011"
_USAGE_DB_NAME = "baostock_guard.db"
_USAGE_MARKER_NAME = "baostock_guard.initialized"
_CREATE_USAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS query_usage (
    usage_date TEXT PRIMARY KEY,
    request_count INTEGER NOT NULL CHECK (request_count >= 0),
    request_limit INTEGER NOT NULL CHECK (request_limit > 0)
)
"""


class BaostockError(RuntimeError):
    """baostock 访问失败的基类。"""


class BaostockLoginError(BaostockError):
    """baostock 登录失败。"""


class BaostockQueryError(BaostockError):
    """baostock 查询调用失败或返回错误码。"""


class BaostockRequestLimitError(BaostockError):
    """baostock 每日查询请求超过安全限额。"""


class BaostockGuardStateError(BaostockError):
    """配额状态不可安全读取或更新。"""


def _usage_path(state_dir: str | Path | None = None) -> Path:
    base_dir = Path(state_dir).expanduser() if state_dir else get_runtime_state_dir()
    return base_dir / _USAGE_DB_NAME


def _usage_marker_path(path: Path) -> Path:
    return path.with_name(_USAGE_MARKER_NAME)


def _state_error(path: Path, exc: Exception) -> BaostockGuardStateError:
    return BaostockGuardStateError(
        f"无法安全读写 baostock 配额状态：{path}。"
        "为避免计数归零后超限，已停止查询；请检查文件权限或修复该状态库。"
    )


def _ensure_usage_schema(path: Path) -> None:
    """并发安全地初始化状态库；已初始化后缺表则保守失败。"""
    marker_path = _usage_marker_path(path)
    if marker_path.exists() and not path.exists():
        raise BaostockGuardStateError(f"baostock 配额状态库丢失，但初始化标记仍存在：{path}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'query_usage'"
            ).fetchone()
            if marker_path.exists() and not table_exists:
                raise BaostockGuardStateError(f"baostock 配额状态库缺少 query_usage 表：{path}")
            if not table_exists:
                other_objects = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
                if other_objects:
                    raise BaostockGuardStateError(
                        f"baostock 配额状态库结构异常，无法安全初始化：{path}"
                    )
                conn.execute(_CREATE_USAGE_TABLE_SQL)
            conn.commit()

        try:
            marker_path.touch(exist_ok=False)
        except FileExistsError:
            pass
    except BaostockGuardStateError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise _state_error(path, exc) from exc


def get_query_usage(state_dir: str | Path | None = None) -> tuple[str, int, int]:
    """返回今天的 baostock 查询计数：(日期, 已用次数, 上限)。"""
    today = date.today().isoformat()
    path = _usage_path(state_dir)
    if not path.exists():
        if _usage_marker_path(path).exists():
            raise BaostockGuardStateError(f"baostock 配额状态库丢失，但初始化标记仍存在：{path}")
        return today, 0, DAILY_QUERY_LIMIT

    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'query_usage'"
            ).fetchone()
            if not table_exists:
                raise BaostockGuardStateError(f"baostock 配额状态库缺少 query_usage 表：{path}")
            row = conn.execute(
                "SELECT request_count FROM query_usage WHERE usage_date = ?",
                (today,),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise _state_error(path, exc) from exc

    try:
        used = int(row[0]) if row else 0
    except (TypeError, ValueError) as exc:
        raise BaostockGuardStateError(
            f"baostock 配额状态包含不可解析的计数：{row[0] if row else None}"
        ) from exc
    if used < 0:
        raise BaostockGuardStateError(f"baostock 配额状态包含非法计数：{used}")
    return today, used, DAILY_QUERY_LIMIT


def claim_query_requests(
    count: int = 1,
    state_dir: str | Path | None = None,
) -> None:
    """在查询前原子领取请求额度，超过 50000 次直接中止。"""
    if count < 1:
        raise ValueError("count 必须大于 0")

    today = date.today().isoformat()
    path = _usage_path(state_dir)
    _ensure_usage_schema(path)

    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT request_count FROM query_usage WHERE usage_date = ?",
                (today,),
            ).fetchone()
            try:
                used = int(row[0]) if row else 0
            except (TypeError, ValueError) as exc:
                raise BaostockGuardStateError(
                    f"baostock 配额状态包含不可解析的计数：{row[0] if row else None}"
                ) from exc

            if used < 0:
                raise BaostockGuardStateError(f"baostock 配额状态包含非法计数：{used}")
            if used + count > DAILY_QUERY_LIMIT:
                raise BaostockRequestLimitError(
                    f"baostock 今日查询请求将超过上限：已用 {used} 次，"
                    f"本次需要 {count} 次，上限 {DAILY_QUERY_LIMIT} 次。"
                    "为避免 IP 进入黑名单，已停止。"
                )

            conn.execute(
                """
                INSERT INTO query_usage (usage_date, request_count, request_limit)
                VALUES (?, ?, ?)
                ON CONFLICT(usage_date) DO UPDATE SET
                    request_count = excluded.request_count,
                    request_limit = excluded.request_limit
                """,
                (today, used + count, DAILY_QUERY_LIMIT),
            )
            conn.commit()
    except (BaostockGuardStateError, BaostockRequestLimitError):
        raise
    except (OSError, sqlite3.Error) as exc:
        raise _state_error(path, exc) from exc


def _login_error_message(result: object) -> str:
    error_code = str(getattr(result, "error_code", ""))
    error_msg = str(getattr(result, "error_msg", ""))
    message = f"baostock 登录失败：error_code={error_code} error_msg={error_msg}"
    if error_code == BLACKLIST_ERROR_CODE:
        message += "；IP已经加入黑名单，需要去QQ群里求助"
    return message


def login_baostock(logger: Any) -> None:
    """登录 baostock，并明确识别网络、服务端和 IP 黑名单错误。"""
    import baostock as bs

    try:
        result = bs.login()
    except Exception as exc:
        message = f"baostock 登录请求异常：{exc}"
        logger.error(message)
        raise BaostockLoginError(message) from exc

    if str(getattr(result, "error_code", "")) != "0":
        message = _login_error_message(result)
        logger.error(message)
        raise BaostockLoginError(message)


def logout_baostock(logger: Any) -> None:
    """退出 baostock；退出失败只记录日志，不覆盖主流程异常。"""
    import baostock as bs

    try:
        bs.logout()
    except Exception as exc:
        logger.warning(f"baostock 退出失败：{exc}")


def _validate_query_result(result: object, operation: str) -> object:
    error_code = str(getattr(result, "error_code", ""))
    if error_code == "0":
        return result
    error_msg = str(getattr(result, "error_msg", ""))
    raise BaostockQueryError(
        f"baostock {operation} 失败：error_code={error_code} error_msg={error_msg}"
    )


def query_history_k_data_plus(
    *args: Any,
    _state_dir: str | Path | None = None,
    **kwargs: Any,
) -> object:
    """受每日限额保护的 query_history_k_data_plus。"""
    import baostock as bs

    claim_query_requests(state_dir=_state_dir)
    try:
        result = bs.query_history_k_data_plus(*args, **kwargs)
    except Exception as exc:
        raise BaostockQueryError(f"baostock query_history_k_data_plus 调用异常：{exc}") from exc
    return _validate_query_result(result, "query_history_k_data_plus")


def query_stock_basic(
    *args: Any,
    _state_dir: str | Path | None = None,
    **kwargs: Any,
) -> object:
    """受每日限额保护的 query_stock_basic。"""
    import baostock as bs

    claim_query_requests(state_dir=_state_dir)
    try:
        result = bs.query_stock_basic(*args, **kwargs)
    except Exception as exc:
        raise BaostockQueryError(f"baostock query_stock_basic 调用异常：{exc}") from exc
    return _validate_query_result(result, "query_stock_basic")
