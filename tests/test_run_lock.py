"""单实例运行锁测试。"""

import pytest

from sequoia_x.core.run_lock import RunLock


def test_second_run_lock_is_rejected(tmp_path) -> None:
    """同一用户级状态目录中只能有一个 Sequoia-X 实例。"""
    with RunLock(tmp_path) as first_lock:
        lock_path = first_lock.path
        assert lock_path.exists()

        with pytest.raises(RuntimeError, match="已有 Sequoia-X 实例"):
            with RunLock(tmp_path):
                pass

    assert lock_path.read_text(encoding="ascii").strip().isdigit()


def test_run_lock_can_be_reacquired_after_exit(tmp_path) -> None:
    """正常退出后，操作系统锁应立即释放。"""
    with RunLock(tmp_path):
        pass
    with RunLock(tmp_path):
        pass
