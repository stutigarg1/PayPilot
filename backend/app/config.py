from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    auth_mode: Literal["local", "supabase"] = "local"
    frontend_url: str = "http://localhost:3000"
    database_url: str = "sqlite:///./paypilot.db"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    model_daily_limit: int = 20
    model_global_daily_limit: int = 100
    model_max_output_tokens: int = 512

    @model_validator(mode="after")
    def validate_deployment(self):
        frontend = urlparse(self.frontend_url)
        if frontend.scheme not in {"http", "https"} or not frontend.netloc:
            raise ValueError("FRONTEND_URL must be an HTTP(S) origin.")
        if frontend.path not in {"", "/"} or frontend.query or frontend.fragment:
            raise ValueError("FRONTEND_URL must not include a path, query, or fragment.")
        self.frontend_url = self.frontend_url.rstrip("/")
        if self.auth_mode == "local":
            if self.app_env != "development" or frontend.hostname not in {
                "localhost", "127.0.0.1", "::1",
            }:
                raise ValueError("Local authentication is only for loopback development.")
        elif not self.supabase_url.startswith("https://") or not self.supabase_anon_key:
            raise ValueError("Supabase authentication requires its HTTPS URL and anon key.")
        if self.app_env == "production" and frontend.scheme != "https":
            raise ValueError("Production requires an HTTPS frontend.")
        if min(self.model_daily_limit, self.model_global_daily_limit,
               self.model_max_output_tokens) < 1:
            raise ValueError("Model usage limits must be positive.")
        return self


@lru_cache
def get_settings():
    return Settings()
