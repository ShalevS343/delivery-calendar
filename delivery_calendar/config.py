"""Settings for delivery-calendar, read from the environment.

Every knob lives in one place so the compose file, the container healthcheck and
the tests all agree on defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """A setting had an unusable value."""


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name}: expected a boolean, got {raw!r}")


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: expected an integer, got {raw!r}") from exc


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: expected a number, got {raw!r}") from exc


def _list(env: Mapping[str, str], name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    # mail
    mail_user: str = ""
    mail_app_password: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    mail_folder: str = "INBOX"
    imap_search: str = (
        "newer_than:7d -category:promotions -category:social -in:spam"
    )
    lookback_days: int = 7
    poll_interval_minutes: int = 60
    max_mails_per_run: int = 50
    # parsing
    parser_languages: tuple[str, ...] = ("he", "en")
    default_country: str = "IL"
    review_min_confidence: float = 0.7
    # calendar
    calendar_auth: str = "service_account"
    calendar_id: str = ""
    calendar_name: str = "Deliveries"
    delivered_retention_days: int = 7
    credentials_file: str = "/secrets/credentials.json"
    token_file: str = "/secrets/token.json"
    service_account_file: str = "/secrets/service-account.json"
    # geo
    geocoder: str = "none"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "delivery-calendar/1.0 (owner)"
    geocode_min_confidence: float = 0.6
    # model (post-v1, off)
    model_enabled: bool = False
    model_path: str = "/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    model_threads: int = 3
    model_ctx: int = 2048
    model_timeout: int = 300
    model_max_calls_per_run: int = 5
    model_min_free_mb: int = 1400
    # paths
    db_path: str = "/data/delivery.db"
    raw_dir: str = "/data/raw"
    state_dir: str = "/data"
    rules_dir: str = "/data/rules"
    log_dir: str = "/logs"
    # runtime
    dry_run: bool = True
    ui_enabled: bool = True
    ui_port: int = 8099
    ui_user: str = "admin"
    ui_password: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        settings = cls(
            # Gmail shows app passwords as four groups of four; the spaces are
            # display only, and IMAP LOGIN rejects them.
            mail_user=env.get("MAIL_USER", "").strip(),
            mail_app_password="".join(env.get("MAIL_APP_PASSWORD", "").split()),
            imap_host=env.get("IMAP_HOST", "imap.gmail.com"),
            imap_port=_int(env, "IMAP_PORT", 993),
            mail_folder=env.get("MAIL_FOLDER", "INBOX"),
            imap_search=env.get("IMAP_SEARCH", cls.imap_search),
            lookback_days=_int(env, "LOOKBACK_DAYS", 7),
            poll_interval_minutes=_int(env, "POLL_INTERVAL_MINUTES", 60),
            max_mails_per_run=_int(env, "MAX_MAILS_PER_RUN", 50),
            parser_languages=_list(env, "PARSER_LANGUAGES", ("he", "en")),
            default_country=env.get("DEFAULT_COUNTRY", "IL"),
            review_min_confidence=_float(env, "REVIEW_MIN_CONFIDENCE", 0.7),
            calendar_auth=env.get("CALENDAR_AUTH", "service_account"),
            calendar_id=env.get("CALENDAR_ID", ""),
            calendar_name=env.get("CALENDAR_NAME", "Deliveries"),
            delivered_retention_days=_int(env, "DELIVERED_RETENTION_DAYS", 7),
            credentials_file=env.get("GOOGLE_CREDENTIALS_FILE", "/secrets/credentials.json"),
            token_file=env.get("GOOGLE_TOKEN_FILE", "/secrets/token.json"),
            service_account_file=env.get(
                "GOOGLE_SA_FILE", "/secrets/service-account.json"
            ),
            geocoder=env.get("GEOCODER", "none"),
            nominatim_url=env.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org"),
            nominatim_user_agent=env.get(
                "NOMINATIM_USER_AGENT", "delivery-calendar/1.0 (owner)"
            ),
            geocode_min_confidence=_float(env, "GEOCODE_MIN_CONFIDENCE", 0.6),
            model_enabled=_flag(env, "MODEL_ENABLED", False),
            model_path=env.get("MODEL_PATH", cls.model_path),
            model_threads=_int(env, "MODEL_THREADS", 3),
            model_ctx=_int(env, "MODEL_CTX", 2048),
            model_timeout=_int(env, "MODEL_TIMEOUT", 300),
            model_max_calls_per_run=_int(env, "MODEL_MAX_CALLS_PER_RUN", 5),
            model_min_free_mb=_int(env, "MODEL_MIN_FREE_MB", 1400),
            db_path=env.get("DB_PATH", "/data/delivery.db"),
            raw_dir=env.get("RAW_DIR", "/data/raw"),
            state_dir=env.get("STATE_DIR", "/data"),
            rules_dir=env.get("RULES_DIR", "/data/rules"),
            log_dir=env.get("LOG_DIR", "/logs"),
            dry_run=_flag(env, "DRY_RUN", True),
            ui_enabled=_flag(env, "UI_ENABLED", True),
            ui_port=_int(env, "UI_PORT", 8099),
            ui_user=env.get("UI_USER", "admin"),
            ui_password=env.get("UI_PASSWORD", ""),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
        )
        if settings.calendar_auth not in {"service_account", "oauth"}:
            raise ConfigError(
                f"CALENDAR_AUTH: expected service_account or oauth, got {settings.calendar_auth!r}"
            )
        if settings.geocoder not in {"none", "nominatim"}:
            raise ConfigError(
                f"GEOCODER: expected none or nominatim, got {settings.geocoder!r}"
            )
        return settings

    def validate(self) -> list[str]:
        """Return human-readable problems that would stop a live run."""
        if self.dry_run:
            return []
        problems: list[str] = []
        if not self.mail_user:
            problems.append("MAIL_USER is empty")
        if not self.mail_app_password:
            problems.append("MAIL_APP_PASSWORD is empty")
        if not self.calendar_id:
            problems.append("CALENDAR_ID is empty (required to write events)")
        if self.calendar_auth == "service_account":
            if not Path(self.service_account_file).is_file():
                problems.append(f"GOOGLE_SA_FILE not found: {self.service_account_file}")
        else:
            if not Path(self.credentials_file).is_file():
                problems.append(f"GOOGLE_CREDENTIALS_FILE not found: {self.credentials_file}")
        return problems
