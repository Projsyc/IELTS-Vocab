"""应用配置 —— 从环境变量 / .env 读取。

所有配置集中在此，不在业务代码里散落 os.getenv()。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # —— 数据库 ——
    DATABASE_URL: str = (
        "postgresql+asyncpg://ielts:ielts_dev_pw@localhost:5432/ielts_vocab"
    )
    DATABASE_URL_SYNC: str = (
        "postgresql+psycopg2://ielts:ielts_dev_pw@localhost:5432/ielts_vocab"
    )
    DB_ECHO: bool = False

    # —— JWT ——
    SECRET_KEY: str = "dev-only-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 天

    # —— CORS ——
    CORS_ORIGINS: str = "http://localhost:5173"

    # —— 音频 ——
    AUDIO_DIR: str = "static/audio"

    # —— LLM（话题打标用，OpenAI 兼容接口）——
    # 换供应商只改这三项，不用改代码。密钥写在 .env，不进 git。
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-v4-flash"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """带缓存，避免每次请求都重新解析 .env。"""
    return Settings()


settings = get_settings()
