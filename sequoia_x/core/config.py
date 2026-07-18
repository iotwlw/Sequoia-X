"""配置管理模块：通过 pydantic-settings 从环境变量或 .env 加载配置。"""

from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from sequoia_x.core.runtime_state import get_runtime_state_dir

_STRATEGY_WEBHOOK_PREFIX = "STRATEGY_WEBHOOK_"
_PLACEHOLDER_MARKERS = ("your-", "example.com/hook")


def is_placeholder_webhook(url: str) -> bool:
    """判断 Webhook 是否仍是仓库示例值。"""
    normalized = url.strip().lower()
    return not normalized or any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


class StrategyWebhookSettingsSource(PydanticBaseSettingsSource):
    """把环境变量和 .env 中的 STRATEGY_WEBHOOK_<KEY> 聚合为字典。"""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self.env_settings = env_settings
        self.dotenv_settings = dotenv_settings

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        webhooks: dict[str, str] = {}
        # .env 先加载，真实系统环境变量覆盖同名策略配置。
        for source in (self.dotenv_settings, self.env_settings):
            env_vars = getattr(source, "env_vars", {})
            for key, value in env_vars.items():
                if not value or not key.upper().startswith(_STRATEGY_WEBHOOK_PREFIX):
                    continue
                if is_placeholder_webhook(value):
                    continue
                strategy_key = key[len(_STRATEGY_WEBHOOK_PREFIX) :].lower()
                webhooks[strategy_key] = value
        return {"strategy_webhooks": webhooks} if webhooks else {}


class Settings(BaseSettings):
    db_path: str = "data/sequoia_v2.db"
    start_date: str = "2024-01-01"
    state_dir: str = Field(
        default_factory=lambda: str(get_runtime_state_dir()),
        validation_alias="SEQUOIA_X_STATE_DIR",
    )
    feishu_webhook_url: str  # 必填字段，缺失时抛出 ValidationError
    strategy_webhooks: dict[str, str] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        strategy_webhooks = StrategyWebhookSettingsSource(
            settings_cls,
            env_settings=env_settings,
            dotenv_settings=dotenv_settings,
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            strategy_webhooks,
            file_secret_settings,
        )

    def get_webhook_url(self, webhook_key: str) -> str:
        """返回策略专属 Webhook；未配置或仍为示例值时回退默认地址。"""
        strategy_url = self.strategy_webhooks.get(webhook_key.lower(), "")
        if strategy_url and not is_placeholder_webhook(strategy_url):
            return strategy_url
        return self.feishu_webhook_url


_settings: Settings | None = None


def get_settings() -> Settings:
    """首次调用时加载并缓存全局 Settings。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
