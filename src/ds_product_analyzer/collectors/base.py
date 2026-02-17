from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawSignalData:
    """A single raw signal from any data source."""

    source: str  # e.g. "google_trends", "reddit", "amazon", "tiktok"
    product_name: str
    signal_type: str  # e.g. "search_volume", "upvote_velocity", "bsr_rank"
    value: float
    metadata: dict = field(default_factory=dict)
    collected_at: datetime = field(default_factory=datetime.utcnow)


class BaseCollector(ABC):
    """Abstract base for all data collectors."""

    source_name: str = "unknown"

    @abstractmethod
    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        """Collect raw signals for given keywords. Returns list of RawSignalData."""
        ...
