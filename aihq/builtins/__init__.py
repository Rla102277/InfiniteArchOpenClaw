"""Built-in connectors. Each module exposes one Connector subclass; `ALL` maps id to class."""
from __future__ import annotations

from .base import Connector  # noqa: F401
from .calendar_app import CalendarConnector
from .gmail import GmailConnector
from .instagram import InstagramConnector
from .site import SiteConnector
from .telegram import TelegramConnector

ALL = {cls.id: cls for cls in (GmailConnector, CalendarConnector, TelegramConnector, InstagramConnector, SiteConnector)}
