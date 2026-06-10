from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from functools import lru_cache
from typing import Tuple, Type
from pathlib import Path
from dotenv import dotenv_values


def _read_env_file() -> dict:
    """Read .env from project root. Returns empty dict if not found."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        return {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    return {}


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    surveillance_api_key: str = ""  # Optional: set to require X-API-Key header on POST endpoints
    jira_base_url: str = ""
    jira_api_token: str = ""
    jira_user_email: str = ""
    jira_project_key: str = "COMP"
    jira_l2_assignee_account_id: str = ""
    slack_webhook_url: str = ""
    slack_channel: str = "#compliance-alerts"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    # Read .env directly so it overrides system env vars (e.g. Claude Code's own key)
    env_vals = _read_env_file()
    return Settings(**{k.lower(): v for k, v in env_vals.items()})
