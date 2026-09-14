"""Source connectors (Brreg open data, NAV job feed)."""
from .brreg import BrregConnector
from .nav import NavFeedConnector

__all__ = ["BrregConnector", "NavFeedConnector"]
