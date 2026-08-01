"""
config.py — Application settings loaded from .env
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in your values."
        )
    return val


class Settings:
    # Auth
    PASSWORD_HASH: str = _require("PASSWORD_HASH")
    JWT_SECRET: str = _require("JWT_SECRET")
    JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

    # Rate limiting
    RATE_LIMIT_MAX_ATTEMPTS: int = int(os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "5"))
    RATE_LIMIT_LOCKOUT_SECONDS: int = int(os.getenv("RATE_LIMIT_LOCKOUT_SECONDS", "900"))

    # ADB
    ADB_DEVICE_SERIAL: str = os.getenv("ADB_DEVICE_SERIAL", "")
    SCREEN_FPS: int = max(1, min(30, int(os.getenv("SCREEN_FPS", "10"))))

    # Server
    SERVER_HOST: str = os.getenv("SERVER_HOST", "127.0.0.1")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8080"))

    # Static files path
    STATIC_DIR: Path = Path(__file__).parent.parent / "static"


settings = Settings()
