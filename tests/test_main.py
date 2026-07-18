"""主程序入口属性测试。"""

import sys
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

# 预先导入 main 模块，避免在 @given 循环中重复导入
import main as main_module


# Feature: sequoia-x-v2, Property 13: 主程序异常以非零退出码终止
@given(error_msg=st.text(min_size=1, max_size=100))
@h_settings(max_examples=30, deadline=None)
def test_main_exits_nonzero_on_exception(error_msg: str) -> None:
    """属性 13：main() 中任意未捕获异常应导致 sys.exit(1)。"""
    with patch.object(sys, "argv", ["main.py"]):
        with patch.object(
            main_module,
            "get_settings",
            side_effect=RuntimeError(error_msg),
        ) as get_settings:
            with pytest.raises(SystemExit) as exc_info:
                main_module.main()

    assert exc_info.value.code == 1
    get_settings.assert_called_once_with()


def test_doctor_mode_does_not_acquire_run_lock() -> None:
    """离线诊断不连接 baostock，因此不应占用生产运行锁。"""
    fake_settings = object()
    with patch.object(sys, "argv", ["main.py", "--doctor"]):
        with patch.object(main_module, "get_settings", return_value=fake_settings):
            with patch.object(main_module, "get_logger") as get_logger:
                with patch.object(main_module, "run_doctor", return_value=True) as run_doctor:
                    with patch.object(main_module, "RunLock") as run_lock:
                        main_module.main()

    run_doctor.assert_called_once_with(fake_settings, get_logger.return_value)
    run_lock.assert_not_called()
