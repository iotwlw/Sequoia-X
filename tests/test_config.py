"""配置管理属性测试。"""

import os

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st
from pydantic import ValidationError

from sequoia_x.core.config import Settings


# Feature: sequoia-x-v2, Property 1: 环境变量覆盖配置默认值
@given(
    db_path=st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="/_.-"
        ),
    )
)
@h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_env_overrides_default(db_path: str, monkeypatch) -> None:
    """属性 1：任意合法 db_path 通过环境变量设置后，Settings 实例应反映该值。"""
    import sequoia_x.core.config as cfg_module

    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(cfg_module, "_settings", None)
    s = Settings(_env_file=None)
    assert s.db_path == db_path


# Feature: sequoia-x-v2, Property 2: 缺失必填字段触发 ValidationError
def test_missing_required_field_raises() -> None:
    """属性 2：缺少 feishu_webhook_url 时，实例化 Settings 应抛出 ValidationError。"""
    # 确保环境变量中没有该字段
    env_backup = os.environ.pop("FEISHU_WEBHOOK_URL", None)
    try:
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "feishu_webhook_url" in str(exc_info.value).lower()
    finally:
        if env_backup is not None:
            os.environ["FEISHU_WEBHOOK_URL"] = env_backup


def test_dotenv_loads_strategy_webhooks_and_state_dir(monkeypatch, tmp_path) -> None:
    """动态策略 Webhook 和共享状态目录都应直接从指定 .env 加载。"""
    monkeypatch.delenv("STRATEGY_WEBHOOK_MA_VOLUME", raising=False)
    monkeypatch.delenv("SEQUOIA_X_STATE_DIR", raising=False)
    env_file = tmp_path / ".env"
    state_dir = (tmp_path / "shared-state").as_posix()
    env_file.write_text(
        "\n".join(
            [
                "FEISHU_WEBHOOK_URL=https://open.feishu.cn/default-real",
                "STRATEGY_WEBHOOK_MA_VOLUME=https://open.feishu.cn/ma-real",
                f"SEQUOIA_X_STATE_DIR={state_dir}",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.state_dir == state_dir
    assert settings.strategy_webhooks == {"ma_volume": "https://open.feishu.cn/ma-real"}
    assert settings.get_webhook_url("ma_volume") == "https://open.feishu.cn/ma-real"


def test_placeholder_strategy_webhook_falls_back_to_default(tmp_path) -> None:
    """复制 .env.example 后只填写默认 URL，不应把策略推送到示例 token。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FEISHU_WEBHOOK_URL=https://open.feishu.cn/default-real",
                "STRATEGY_WEBHOOK_TURTLE=https://open.feishu.cn/your-turtle-token",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert "turtle" not in settings.strategy_webhooks
    assert settings.get_webhook_url("turtle") == settings.feishu_webhook_url


def test_state_dir_can_be_passed_by_field_name(tmp_path) -> None:
    """程序化构造 Settings 时，state_dir 字段名不能被 extra=ignore 吞掉。"""
    state_dir = str(tmp_path / "explicit-state")
    settings = Settings(
        state_dir=state_dir,
        feishu_webhook_url="https://open.feishu.cn/default-real",
        _env_file=None,
    )

    assert settings.state_dir == state_dir
