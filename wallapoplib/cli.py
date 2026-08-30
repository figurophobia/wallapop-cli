"""Argparse wiring: search/deal flags, subcommands and the main() entry."""

from __future__ import annotations

import argparse
import sys

from .commands import (
    cmd_deals,
    cmd_login,
    cmd_logout,
    cmd_message,
    cmd_offer,
    cmd_replies,
    cmd_search,
    cmd_session,
    cmd_watch,
)
from .config import CATEGORIES, CITIES, ORDER_ALIASES, VERSION
from .http import ApiError
from .output import eprint, paint

def add_search_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--category", type=int, help=f"category id. common: {', '.join(f'{k}({v})' for k, v in sorted(CATEGORIES.items()))}")
    sp.add_argument("--subcat", help="filter by taxonomy leaf id, e.g. 10088 = consolas, 10092 = accesorios de consola")
    sp.add_argument("--min-price", type=float)
    sp.add_argument("--max-price", type=float)
    sp.add_argument("--city", help=f"city preset: {', '.join(sorted(CITIES))}")
    sp.add_argument("--lat", type=float)
    sp.add_argument("--lon", type=float)
    sp.add_argument("--order", choices=sorted(ORDER_ALIASES), default="relevance")
    sp.add_argument("--pages", type=int, default=1, help="result pages to fetch (default 1)")
    sp.add_argument("--delay", type=float, default=1.5, help="seconds to wait between page fetches")
    sp.add_argument("--ship-only", action="store_true", help="only keep listings that can be shipped")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--csv", action="store_true")
    sp.add_argument("--quiet", action="store_true")


def add_deal_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--min-discount", type=float, default=0.15, help="require at least this discount vs median (default 0.15)")
    sp.add_argument("--top", type=int, default=10, help="act on the best N deals only (default 10)")
    sp.add_argument("--offer-pct", type=float, help="auto-send an offer at this fraction of the asking price, e.g. 0.8")
    sp.add_argument(
        "--msg",
        help="auto-message template. placeholders: {title} {price} {link} {city} {offer}",
    )
    sp.add_argument("--dry-run", action="store_true", help="print intent without sending")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallapop",
        description="Wallapop automation CLI: search, hunt deals, send offers and messages.",
        epilog=(
            "examples:\n"
            "  wallapop search 'nintendo switch' --city madrid --order price_asc\n"
            "  wallapop deals 'ps5' --max-price 250 --min-discount 0.2\n"
            "  wallapop offer 123456789 --pct 0.8 --msg 'Hola, aceptarias 200?'\n"
            "  wallapop message nintendo-switch-lite-blu-1295309387 --text 'Hola, funciona bien?'\n"
            "  wallapop replies --conversation nzxp0m9qp5j2\n"
            "  wallapop watch 'steam deck' --max-price 300 --msg 'hola amigo, funciona bien?' --interval 600\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    lp = sub.add_parser("login", help="log in and store credentials (prompts for password)")
    lp.add_argument("email", nargs="?", default=None, help="email for password login")
    lp.add_argument("password", nargs="?", help="optional; if omitted you are prompted safely")
    lp.add_argument("--browser", choices=["firefox"], help="import your session from the Firefox profile (works with Google accounts)")
    lp.add_argument("--cookies", help="paste cookies as a 'name=value; ...' string (accessToken, __Secure-next-auth.session-token)"
    )
    lp.add_argument("--cookies-file", default="cookies.txt", help="read cookies from this file instead of --cookies (default: cookies.txt)")
    lp.set_defaults(func=cmd_login)

    so = sub.add_parser("logout", help="clear stored credentials")
    so.set_defaults(func=cmd_logout)

    ss = sub.add_parser("session", help="show current session")
    ss.set_defaults(func=cmd_session)

    se = sub.add_parser("search", help="search listings")
    add_search_args(se)
    se.add_argument("keyword", nargs="+")
    se.set_defaults(func=cmd_search)

    dl = sub.add_parser("deals", help="find listings priced below market median")
    add_search_args(dl)
    add_deal_args(dl)
    dl.add_argument("keyword", nargs="+")
    dl.set_defaults(func=cmd_deals)

    wc = sub.add_parser("watch", help="loop a scan and auto-offer/message fresh deals")
    add_search_args(wc)
    add_deal_args(wc)
    wc.add_argument("--interval", type=float, default=300, help="seconds between scans (default 300, min 5)")
    wc.add_argument("--once", action="store_true", help="run a single scan then exit")
    wc.add_argument("keyword", nargs="+")
    wc.set_defaults(func=cmd_watch)

    of = sub.add_parser("offer", help="make/check an offer on an item, or accept/decline/counter an existing offer")
    of.add_argument("what", nargs="?", help="item id/url/slug, offer id, or an action: accept, decline, counter, status")
    of.add_argument("offer_id", nargs="?", help="offer id (required with an action)")
    of.add_argument("--price", type=float, help="exact offer amount")
    of.add_argument("--pct", type=float, help="offer as a fraction of the asking price, e.g. 0.8")
    of.add_argument("--msg", help="optional message attached to the offer")
    of.add_argument("--last", type=int, default=None, help="offer status: only show the N most recent conversations")
    of.add_argument("--dry-run", action="store_true")
    of.set_defaults(func=cmd_offer)

    me = sub.add_parser("message", help="send a chat message to an item's seller")
    me.add_argument("item_id", help="item url, web slug or internal hash")
    me.add_argument("--text", required=True, help="message text")
    me.add_argument("--dry-run", action="store_true")
    me.set_defaults(func=cmd_message)

    rp = sub.add_parser("replies", help="list your inbox conversations (or print one full thread)")
    rp.add_argument("--conversation", help="conversation id to dump the full message thread")
    rp.add_argument("--json", action="store_true", help="dump raw JSON (inbox or conversation thread)")
    rp.set_defaults(func=cmd_replies)

    parser.add_argument("--version", action="version", version=f"wallapop-cli {VERSION}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ApiError as err:
        eprint(paint.red(f"error: {err}"))
        return 1
