"""Explicit configuration loading; importing this module does not read secrets."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Required ActivityInfo configuration is missing or invalid."""


@dataclass(frozen=True)
class ActivityInfoConfig:
    database_id: str
    api_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,31}", self.database_id):
            raise ConfigurationError("ACTIVITYINFO_DATABASE_ID must be a resource ID")
        if not self.api_token.strip() or any(c.isspace() for c in self.api_token):
            raise ConfigurationError("ACTIVITYINFO_API_TOKEN is missing or invalid")

    @classmethod
    def from_environment(cls, *, env_file: Path | None = None):
        """Read environment values, optionally loading one explicitly chosen file.

        Existing environment variables take precedence. No ancestor-file search.
        """
        cloud_runtime = os.environ.get("GAE_ENV") == "standard"
        if env_file is not None and not cloud_runtime:
            load_dotenv(env_file, override=False, interpolate=False)
        token = os.environ.get("ACTIVITYINFO_API_TOKEN")
        if token is None and cloud_runtime:
            token = cloud_token()
        return cls(
            database_id=os.environ.get("ACTIVITYINFO_DATABASE_ID", ""),
            api_token=token or "",
        )


def cloud_token() -> str:
    """Read one explicitly selected version lazily, using platform credentials.

    Production pins an enabled version during bootstrap: Accessor does not need
    permission to list versions. Rotation updates this non-secret version setting.
    No token is cached on disk, logged, or attached to propagated errors.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    version = os.environ.get("ACTIVITYINFO_SECRET_VERSION", "latest")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise ConfigurationError("Google Cloud runtime project is missing or invalid")
    if version != "latest" and not re.fullmatch(r"[1-9][0-9]*", version):
        raise ConfigurationError("Secret version must be a positive integer or latest")
    try:
        from google.cloud import secretmanager

        with secretmanager.SecretManagerServiceClient() as client:
            response = client.access_secret_version(
                request={"name": f"projects/{project}/secrets/activityinfo-api-token/versions/{version}"},
                timeout=10,
            )
            return response.payload.data.decode("utf-8")
    except Exception:
        raise ConfigurationError("ActivityInfo runtime secret could not be loaded") from None
