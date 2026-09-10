"""应用配置。

所有业务阈值集中于此，便于测试时按用例覆盖（见 tests/conftest.py）。
环境变量前缀 ``CRM_``，例如 ``CRM_DATABASE_URL``。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CRM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CRM 拜访与项目管理系统"
    debug: bool = False
    version: str = "0.1.0"

    # --- 存储 -------------------------------------------------------------
    database_url: str = "sqlite:///./data/crm.db"
    upload_dir: str = "./data/uploads"

    # --- 会话 -------------------------------------------------------------
    secret_key: str = "dev-secret-key-change-in-production"
    session_cookie: str = "crm_session"
    session_max_age: int = 8 * 3600

    #: 口令散列轮数。生产用 60 万轮（OWASP 建议）；
    #: 测试环境必须调低，否则每次建种子用户都要数秒，测试套件会被拖垮。
    pbkdf2_iterations: int = 600_000

    # --- 业务阈值（与 app/constants.py 对应，可被环境变量覆盖） ----------
    checkin_early_tolerance_min: int = 30      # R-01
    fence_meters: int = 1000                   # R-03
    min_visit_minutes: int = 5                 # R-05
    min_content_chars: int = 20                # R-11
    offline_time_drift_min: int = 30           # R-10
    edit_window_hours: int = 24                # R-12
    auto_close_hours: int = 24                 # 状态机
    max_upload_bytes: int = 20 * 1024 * 1024   # R-08
    max_images_per_record: int = 9             # R-08
    amount_change_threshold: float = 0.20      # R-26
    override_reason_min_chars: int = 30        # R-21
    rollback_reason_min_chars: int = 20        # R-22
    report_valid_days: int = 90                # 报备有效期

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
