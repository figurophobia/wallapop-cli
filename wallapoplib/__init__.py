"""Wallapop CLI internals (package split of the original wallapop.py).

Modules:
- config   : API constants, paths, credential/session/contacted storage, cookie import
- http     : rate-limited HTTP transport (urllib) + Response wrapper
- tokens   : JWT parsing, token expiry policy
- models   : item normalization, deal ranking, offer-ledger parsing
- ids      : resolve item url/slug/hash -> internal item id
- output   : terminal rendering (colors, tables, announce, thread/offer printing)
- client   : the Wallapop API client (every endpoint call) + browser send fallback
- commands : CLI command handlers (login, search, deals, watch, offer, message, replies)
- cli      : argparse wiring + main()
"""

from .config import VERSION

__version__ = VERSION