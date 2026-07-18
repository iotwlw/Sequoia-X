"""运行时状态目录：跨仓库副本共享单实例锁和 baostock 配额。"""

import os
from pathlib import Path

STATE_DIR_ENV = "SEQUOIA_X_STATE_DIR"


def get_runtime_state_dir() -> Path:
    """返回当前用户级运行状态目录。

    默认使用 ``~/.sequoia-x``。测试或受管部署可通过
    ``SEQUOIA_X_STATE_DIR`` 指向隔离目录。
    """
    configured = os.getenv(STATE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".sequoia-x"
