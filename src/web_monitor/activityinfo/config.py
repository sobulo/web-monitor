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
        if env_file is not None:
            load_dotenv(env_file, override=False, interpolate=False)
        return cls(
            database_id=os.environ.get("ACTIVITYINFO_DATABASE_ID", ""),
            api_token=os.environ.get("ACTIVITYINFO_API_TOKEN", ""),
        )
