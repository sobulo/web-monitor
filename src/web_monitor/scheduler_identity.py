"""Validated Scheduler metadata; authorization belongs to App Engine handlers."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from web_monitor.activityinfo.persistence import read_timestamp, timestamp
from web_monitor.activityinfo.schema import stable_id


class InvalidInvocation(ValueError):
    """Missing or unexpected scheduler identity, without echoing supplied headers."""


@dataclass(frozen=True)
class SchedulerInvocation:
    job_name: str
    scheduled_at: datetime

    def __post_init__(self):
        if not re.fullmatch(r"projects/[a-z][a-z0-9-]+/locations/[a-z0-9-]+/jobs/[a-zA-Z0-9_-]+", self.job_name):
            raise InvalidInvocation("Invalid scheduler job identity")
        try:
            timestamp(self.scheduled_at)
        except ValueError:
            raise InvalidInvocation("Invalid scheduler time") from None
        object.__setattr__(self, 'scheduled_at', self.scheduled_at.astimezone(timezone.utc))

    @property
    def key(self) -> str:
        return stable_id("scheduler", self.job_name, timestamp(self.scheduled_at))

    def crawl_id(self, site_id: str) -> str:
        return stable_id(self.key, site_id, "crawl")

    def snapshot_id(self, site_id: str) -> str:
        return stable_id(self.key, site_id, "snapshot")

    @classmethod
    def from_headers(cls, headers, *, project: str, location: str, job_name: str):
        if not all((project, location, job_name)):
            raise InvalidInvocation("Scheduler is not configured")
        expected = f"projects/{project}/locations/{location}/jobs/{job_name}"
        supplied = headers.get('X-CloudScheduler-JobName', '')
        marker = headers.get('X-CloudScheduler')
        if supplied not in (expected, job_name) or (marker is not None and marker.lower() != 'true'):
            raise InvalidInvocation("Unexpected scheduler identity")
        value = headers.get('X-CloudScheduler-ScheduleTime', '')
        # RFC3339, requiring an explicit offset (including UTC Z).
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise InvalidInvocation("Invalid scheduler time")
        try:
            # App Engine delivery can use the short job name. Normalize it to
            # the configured project/location identity, never an arbitrary job.
            return cls(expected, read_timestamp(value))
        except ValueError:
            raise InvalidInvocation("Invalid scheduler time") from None
