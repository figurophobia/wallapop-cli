"""wallapoplib: typed Wallapop API client + CLI, without a browser.

Public surface (the v1 contract):

- ``Wallapop`` client with ``item``/``search``/``categories``/
  ``send_offer``/
  ``offer_response``/``counter_offer``/``send_message``/``conversation_thread``/
  ``inbox``/``make_offer_info`` and login/session helpers.
- Domain dataclasses ``Item``, ``Deal``, ``OfferEvent``, ``OfferState`` and the
  derivations ``parse_offer_events``, ``offer_state_summary``, ``rank_deals``.
- ``resolve_internal_id`` for item references.
- ``ApiError`` / ``Response`` from the HTTP layer.
- Session aids ``load_credentials`` / ``load_session`` / ``save_session``.

The bundled CLI lives in ``wallapoplib.cli`` (entry point ``wallapop``) and is
not part of the library contract. Requires Python >= 3.10, zero mandatory
dependencies.
"""

from .config import VERSION as __version__
from .config import CATEGORIES, load_credentials, load_session, save_session
from .http import ApiError, Response
from .ids import resolve_internal_id
from .models import (
    Deal,
    Item,
    OfferEvent,
    OfferState,
    offer_state_summary,
    parse_offer_events,
    rank_deals,
)
from .client import Wallapop

__all__ = [
    "Wallapop",
    "Item",
    "Deal",
    "OfferEvent",
    "OfferState",
    "parse_offer_events",
    "offer_state_summary",
    "rank_deals",
    "resolve_internal_id",
    "ApiError",
    "Response",
    "CATEGORIES",
    "load_credentials",
    "load_session",
    "save_session",
    "__version__",
]