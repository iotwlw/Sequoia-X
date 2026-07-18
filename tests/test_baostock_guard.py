"""baostock 访问守卫测试。"""

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sequoia_x.data.baostock_guard import (
    BaostockGuardStateError,
    BaostockLoginError,
    BaostockQueryError,
    BaostockRequestLimitError,
    claim_query_requests,
    get_query_usage,
    login_baostock,
    query_stock_basic,
)


def _claim_many(state_dir: str, count: int) -> None:
    for _ in range(count):
        claim_query_requests(state_dir=state_dir)


def test_login_blacklist_error_is_explicit(monkeypatch) -> None:
    """baostock 返回 10001011 时，应明确提示 IP 黑名单。"""
    fake_bs = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="10001011", error_msg="blocked")
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)
    logger = MagicMock()

    with pytest.raises(BaostockLoginError) as exc_info:
        login_baostock(logger)

    message = str(exc_info.value)
    assert "10001011" in message
    assert "IP已经加入黑名单" in message
    logger.error.assert_called_once()


def test_query_limit_stops_before_exceeding_50000(tmp_path) -> None:
    """每日查询次数达到 50000 后，下一次请求应被拒绝。"""
    claim_query_requests(49_999, state_dir=tmp_path)
    claim_query_requests(1, state_dir=tmp_path)

    _, used, limit = get_query_usage(state_dir=tmp_path)
    assert used == 50_000
    assert limit == 50_000

    with pytest.raises(BaostockRequestLimitError):
        claim_query_requests(1, state_dir=tmp_path)


def test_query_claims_are_atomic_across_threads(tmp_path) -> None:
    """并发领取请求额度时不能丢计数，也不能争用固定临时文件。"""
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(claim_query_requests, 1, state_dir=tmp_path) for _ in range(100)]
        for future in futures:
            future.result()

    _, used, _ = get_query_usage(state_dir=tmp_path)
    assert used == 100


def test_query_claims_are_atomic_across_processes(tmp_path) -> None:
    """不同进程共享同一用户状态目录时，配额领取仍必须精确。"""
    context = get_context("spawn")
    processes = [context.Process(target=_claim_many, args=(str(tmp_path), 20)) for _ in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    _, used, _ = get_query_usage(state_dir=tmp_path)
    assert used == 80


def test_corrupted_usage_database_fails_closed(tmp_path) -> None:
    """配额库损坏时必须停止请求，不能把历史用量静默归零。"""
    (tmp_path / "baostock_guard.db").write_bytes(b"not-a-sqlite-database")

    with pytest.raises(BaostockGuardStateError):
        claim_query_requests(state_dir=tmp_path)


def test_existing_usage_database_without_schema_fails_closed(tmp_path) -> None:
    """状态库存在但核心表丢失时，不能把配额当作零重新初始化。"""
    with sqlite3.connect(tmp_path / "baostock_guard.db"):
        pass
    (tmp_path / "baostock_guard.initialized").touch()

    with pytest.raises(BaostockGuardStateError, match="缺少 query_usage 表"):
        get_query_usage(state_dir=tmp_path)
    with pytest.raises(BaostockGuardStateError, match="缺少 query_usage 表"):
        claim_query_requests(state_dir=tmp_path)


def test_rejected_first_bulk_claim_keeps_state_recoverable(tmp_path) -> None:
    """首次批量领取越界后，下一次正常领取仍应使用有效 schema。"""
    with pytest.raises(BaostockRequestLimitError):
        claim_query_requests(50_001, state_dir=tmp_path)

    claim_query_requests(state_dir=tmp_path)
    _, used, _ = get_query_usage(state_dir=tmp_path)
    assert used == 1


def test_query_error_code_is_wrapped(monkeypatch, tmp_path) -> None:
    """查询返回非零错误码时，应抛出统一的查询异常。"""
    monkeypatch.setenv("SEQUOIA_X_STATE_DIR", str(tmp_path))
    fake_bs = SimpleNamespace(
        query_stock_basic=lambda **kwargs: SimpleNamespace(
            error_code="1001",
            error_msg="bad request",
        )
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)

    with pytest.raises(BaostockQueryError, match="1001"):
        query_stock_basic(code="sh.600000")

    _, used, _ = get_query_usage(state_dir=tmp_path)
    assert used == 1
