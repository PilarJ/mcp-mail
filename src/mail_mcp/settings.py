"""Configuration for one mailbox, loaded from environment / .env.

One process serves exactly one mailbox. Running several mailboxes means
running this image several times with different configuration, which keeps
a compromise of one mailbox from reaching the others.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Identity of this instance. ---
    # Shown in the server name, its instructions and every tool description, so
    # a client with several mail connectors can tell them apart.
    mailbox_label: str = Field(alias="MAILBOX_LABEL")

    base_url: str = Field(alias="BASE_URL")

    # --- Inbound auth: who may talk to this MCP server. ---
    github_client_id: str = Field(alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(alias="GITHUB_CLIENT_SECRET")
    allowed_github_logins_raw: str = Field(default="", alias="ALLOWED_GITHUB_LOGINS")
    jwt_signing_key: str | None = Field(default=None, alias="JWT_SIGNING_KEY")

    # --- IMAP. ---
    imap_host: str = Field(alias="IMAP_HOST")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    imap_user: str = Field(alias="IMAP_USER")
    imap_pass: str = Field(alias="IMAP_PASS")

    # --- SMTP. ---
    smtp_host: str = Field(alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    smtp_user: str = Field(alias="SMTP_USER")
    smtp_pass: str = Field(alias="SMTP_PASS")
    from_name: str = Field(default="", alias="FROM_NAME")

    # --- The hard limit on where mail can go. ---
    # Entries are either a full address or "@domain" for everyone at a domain;
    # "*" alone permits any recipient.
    # Empty disables sending entirely: an unset allowlist must fail closed.
    allowed_recipients_raw: str = Field(default="", alias="ALLOWED_RECIPIENTS")

    # Optional guard against an agent sending the same person mail repeatedly.
    # 0 disables it.
    dedup_minutes: int = Field(default=0, alias="DEDUP_MINUTES")

    # Registers mail_delete, which expunges for good. Off unless asked for.
    allow_delete: bool = Field(default=False, alias="ALLOW_DELETE")

    # Sole approver for sending one specific message outside the allowlist.
    # Unset: mail_request_approval and mail_send_approved are not registered at all.
    approver_address: str = Field(default="", alias="APPROVER_ADDRESS")
    approval_ttl_hours: int = Field(default=72, alias="APPROVAL_TTL_HOURS")
    approval_max_per_hour: int = Field(default=5, alias="APPROVAL_MAX_PER_HOUR")
    approval_max_pending: int = Field(default=10, alias="APPROVAL_MAX_PENDING")

    max_body_chars: int = Field(default=50_000, alias="MAX_BODY_CHARS")
    max_attachment_bytes: int = Field(default=5_000_000, alias="MAX_ATTACHMENT_BYTES")

    data_dir: Path = Field(default=Path("/data"), alias="DATA_DIR")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @staticmethod
    def _split(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def allowed_github_logins(self) -> list[str]:
        return self._split(self.allowed_github_logins_raw)

    @property
    def allowed_recipients(self) -> list[str]:
        return self._split(self.allowed_recipients_raw)

    @property
    def sent_log_path(self) -> Path:
        return self.data_dir / "sent_log.json"

    @property
    def approval_db_path(self) -> Path:
        return self.data_dir / "approvals.sqlite3"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
