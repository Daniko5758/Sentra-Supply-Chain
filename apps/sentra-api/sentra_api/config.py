from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """PRD 6.5 environment names; integration secrets are optional until used."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    aicore_client_id: SecretStr | None = None
    aicore_client_secret: SecretStr | None = None
    aicore_auth_url: str | None = None
    aicore_base_url: str | None = None
    aicore_resource_group: str = "default"
    deployment_rpt: str | None = None
    sentra_service_token: SecretStr | None = None
    agent_runtime_arn: str | None = None
    # `agentcore` calls the AgentCore runtime; `fake` runs the tools in-process without an LLM (local tests, no AWS);
    # `off` starts no worker, so runs stay QUEUED (the test suite).
    agent_runner: Literal["agentcore", "fake", "off"] = "agentcore"
    # Warm session pool (B-13b, DEC-016, contract D.1): ids already warmed with the `ping` task. Keep-alive and the warm-up
    # at start run only for rehearsal and demo, so they are off by default.
    agent_warm_pool_size: int = Field(2, ge=0)
    agent_keepalive_enabled: bool = False
    agent_keepalive_min: float = Field(5, gt=0)  # must stay below the idle limit of an AgentCore session (SP-11)
    aws_region: Literal["ap-southeast-2"] = "ap-southeast-2"
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_app_password: SecretStr | None = None
    mail_from: str | None = None
    role_email_buyer: str | None = None
    role_email_ppic: str | None = None
    role_email_sales: str | None = None
    role_email_scm_manager: str | None = None
    # v1.11 (DEC-039): executives that join an Executive Meeting Room (R-22); demo: team members' addresses are fine.
    role_email_cfo: str | None = None
    role_email_coo: str | None = None
    role_email_ceo: str | None = None
    role_email_legal: str | None = None
    # R-22 limits and meeting timing (section 3 of V3-B_BACKEND).
    scm_approval_limit_idr: int = 100_000_000
    tariff_escalation_duty_idr: int = 250_000_000
    close_call_band_pp: int = 2
    meeting_start_delay_min: int = 10
    meeting_duration_tier1_min: int = 30
    meeting_duration_tier2_min: int = 40
    # Zoom Server-to-Server OAuth. `zoom_user_id` is the e-mail of the host (an S2S account does not accept `me`).
    zoom_account_id: str | None = None
    zoom_client_id: SecretStr | None = None
    zoom_client_secret: SecretStr | None = None
    zoom_user_id: str | None = None
    zoom_fallback_join_url: str | None = None
    demo_users: SecretStr | None = None
    mock_s4_base_url: str = "http://localhost:8000/mock-s4"
    mock_s4_token: SecretStr | None = None
    app_base_url: str = "http://localhost:8000"
    sim_reply_delay_s: int = 75
    feature_erp_execution: bool = False
    feature_meeting_room: bool = False
    feature_whatif: bool = False
    scan_interval_min: int = 120
    daily_check_time: str = "07:00"
    tz: str = "Asia/Jakarta"
