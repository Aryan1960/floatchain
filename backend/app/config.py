from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    csfloat_api_key: str = ""
    cors_origins: str = "http://localhost:5173"

    csgo_api_base: str = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en"
    csfloat_api_base: str = "https://csfloat.com/api/v1"

    cache_dir: str = ".cache"
    catalog_cache_ttl_seconds: int = 24 * 60 * 60
    csfloat_cache_ttl_seconds: int = 300
    csfloat_min_request_interval_seconds: float = 1.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
