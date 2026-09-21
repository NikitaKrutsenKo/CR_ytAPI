"""YouTube API integration package: clients, discovery collectors, quotas, and profiles."""

from research.api.client import (
    RequestCancelled,
    YouTubeApiError,
    YouTubeClient,
    YouTubeQuotaExceeded,
)
from research.api.discovery import (
    YouTubeDiscoveryCollector,
    observation,
)
from research.api.profiles import ApiProfiles
from research.api.quota import (
    QuotaEstimate,
    QuotaManager,
    QuotaStopped,
)
from research.api.services import (
    YouTubeChannelService,
    YouTubeSearchService,
    YouTubeVideoService,
)

__all__ = [
    "ApiProfiles",
    "QuotaEstimate",
    "QuotaManager",
    "QuotaStopped",
    "RequestCancelled",
    "YouTubeApiError",
    "YouTubeChannelService",
    "YouTubeClient",
    "YouTubeDiscoveryCollector",
    "YouTubeQuotaExceeded",
    "YouTubeSearchService",
    "YouTubeVideoService",
    "observation",
]
