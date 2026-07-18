"""运行锁：防止多个 Sequoia-X 实例同时连接 baostock。"""

import os
from io import BufferedRandom
from pathlib import Path

from sequoia_x.core.runtime_state import get_runtime_state_dir


class RunLock:
    """跨平台的单实例文件锁，进程退出后系统会自动释放。"""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        base_dir = Path(state_dir).expanduser() if state_dir else get_runtime_state_dir()
        self.path = base_dir / "sequoia_x.lock"
        self._file: BufferedRandom | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._file = self.path.open("a+b")
            self._file.seek(0, os.SEEK_END)
            if self._file.tell() == 0:
                self._file.write(b"0")
                self._file.flush()
            self._file.seek(0)

            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self._file is not None:
                self._file.close()
                self._file = None
            raise RuntimeError(
                "检测到已有 Sequoia-X 实例正在运行。"
                "为避免 baostock 并发连接和 IP 黑名单风险，本次启动已停止。"
            ) from exc

        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()).encode("ascii"))
        self._file.flush()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._file is None:
            return

        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
