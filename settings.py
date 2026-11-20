from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    auth_mode: str = "dev"
    database_url: str = "postgresql+psycopg://nexusrelay:nexusrelay@localhost:5432/nexusrelay"
    redis_url: str = "redis://localhost:6379/0"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "nexusrelay"
    openai_api_key: str | None = None
    openai_model: str = "gpt-6.2-astra"
    github_repository: str = ""
    github_base_branch: str = "main"
    slack_channel_id: str = ""
    mcp_github_url: str = ""
    mcp_github_bearer_token: str | None = None
    mcp_slack_url: str = ""
    mcp_slack_bearer_token: str | None = None
    mcp_google_drive_url: str = ""
    mcp_microsoft_graph_url: str = ""
    mcp_github_create_branch_tool: str = "create_branch"
    mcp_github_push_files_tool: str = "push_files"
    mcp_github_search_prs_tool: str = "search_pull_requests"
    mcp_github_create_pr_tool: str = "create_pull_request"
    mcp_slack_post_message_tool: str = "slack_send_message"
    mcp_drive_search_tool: str = "search_files"
    mcp_drive_get_file_tool: str = "get_file"
    mcp_drive_create_file_tool: str = "create_file"
    mcp_graph_search_mail_tool: str = "search_messages"
    mcp_graph_send_mail_tool: str = "send_mail"
    mcp_graph_create_document_tool: str = "create_document"
    token_vault_key_b64: str = Field(default="", repr=False)
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = Field(default="", repr=False)
    oauth_github_token_url: str = "https://github.com/login/oauth/access_token"
    oauth_github_authorize_url: str = "https://github.com/login/oauth/authorize"
    oauth_github_redirect_uri: str = ""
    oauth_slack_client_id: str = ""
    oauth_slack_client_secret: str = Field(default="", repr=False)
    oauth_slack_token_url: str = "https://slack.com/api/oauth.v2.access"
    oauth_slack_authorize_url: str = "https://slack.com/oauth/v2/authorize"
    oauth_slack_redirect_uri: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = Field(default="", repr=False)
    oauth_google_token_url: str = "https://oauth2.googleapis.com/token"
    oauth_google_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    oauth_google_redirect_uri: str = ""
    oauth_microsoft_client_id: str = ""
    oauth_microsoft_client_secret: str = Field(default="", repr=False)
    oauth_microsoft_token_url: str = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    oauth_microsoft_authorize_url: str = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    oauth_microsoft_redirect_uri: str = ""
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    frontend_public_url: str = "http://localhost:3000"
    frontend_webhook_url: str | None = None
    frontend_webhook_secret: str = Field(default="", repr=False)
    stripe_secret_key: str | None = Field(default=None, repr=False)
    stripe_issuing_cardholder_id: str | None = None
    oacp_token_signing_key: str = Field(default="", repr=False)
    oacp_admin_token: str = Field(default="", repr=False)
    commerce_token_ttl_seconds: int = 300

    @property
    def temporal_target(self) -> str:
        return self.temporal_address


@lru_cache
def get_settings() -> Settings:
    return Settings()

