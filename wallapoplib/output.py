"""Terminal rendering: colors, tables, announce result, thread/offer printing."""

from __future__ import annotations

import csv
import datetime
import json
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

from .models import offer_payload_summary


@dataclass
class Paint:
    enabled: bool = field(default_factory=lambda: sys.stdout.isatty())

    def _p(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def green(self, text: str) -> str:
        return self._p(text, "32")

    def red(self, text: str) -> str:
        return self._p(text, "31")

    def yellow(self, text: str) -> str:
        return self._p(text, "33")

    def cyan(self, text: str) -> str:
        return self._p(text, "36")

    def bold(self, text: str) -> str:
        return self._p(text, "1")

    def dim(self, text: str) -> str:
        return self._p(text, "2")


paint = Paint()


def truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(width - 1, 1)] + "\u2026"


def clock() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def emit_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def emit_csv(items: list[dict]) -> None:
    fieldnames = ["id", "title", "price", "currency", "city", "user_id", "user_name", "url", "shippable", "discount", "median"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for it in items:
        writer.writerow(it)


def render_template(template: str, it: dict, amount: Optional[float] = None) -> str:
    price = f"{it['price']:.2f}"
    text = template.replace("{title}", it["title"])
    text = text.replace("{price}", price)
    text = text.replace("{link}", it["url"])
    text = text.replace("{city}", it["city"])
    if amount is not None:
        text = text.replace("{offer}", f"{amount:.2f}")
    return text


def print_items(items: list[dict]) -> None:
    if not items:
        print("no results")
        return
    width = shutil.get_terminal_size((100, 24)).columns
    title_w = max(20, width - 65)
    print(paint.bold(f"{'#':>3}  {'PRICE':>9}  {'CTY':<11}  {'SELLER':<12}  SHP  TITLE"))
    for idx, it in enumerate(items, 1):
        ship = paint.green("yes") if it["shippable"] else paint.dim("no")
        row = (
            f"{idx:>3}  {paint.cyan(f'{it['price']:8.2f}')}  {truncate(it['city'], 11):<11}  "
            f"{truncate(it['user_name'] or it['user_id'], 12):<12}  {ship}  {truncate(it['title'], title_w)}"
        )
        print(row)
        if it["url"]:
            print(f"     {paint.dim(it['url'])}")


def print_deals(deals: list[dict], show_discount_bars: bool = True) -> None:
    if not deals:
        print("no deals at the current threshold")
        return
    width = shutil.get_terminal_size((100, 24)).columns
    title_w = max(20, width - 69)
    print(paint.bold(f"{'#':>3}  {'DEAL':>5}  {'PRICE':>9}  {'MEDIAN':>9}  {'CTY':<10}  SHP  TITLE"))
    for idx, it in enumerate(deals, 1):
        pct = it["discount"] * 100
        if it["discount"] > 0:
            deal_tag = paint.green(f"{-pct:4.0f}%")
        elif it["discount"] == 0:
            deal_tag = paint.dim("  0%")
        else:
            deal_tag = paint.red(f"+{abs(pct):3.0f}%")
        ship = paint.green("yes") if it["shippable"] else paint.dim("no")
        row = (
            f"{idx:>3}  {deal_tag}  {paint.cyan(f'{it['price']:8.2f}')}  {paint.dim(f'{it['median']:8.2f}')}  "
            f"{truncate(it['city'], 10):<10}  {ship}  {truncate(it['title'], title_w)}"
        )
        print(row)
        if it["url"]:
            print(f"     {paint.dim(it['url'])}")


def announce(label: str, resp: Optional[Response]) -> None:
    if resp is None:
        print(paint.yellow(f"[dry-run] would {label}"))
        return
    if resp.ok:
        print(paint.green(f"[ok] {label}"))
        detail = resp.error_reason
        if detail and detail.lower() not in ("ok", "done", "success"):
            print(f"      {detail}")
    else:
        print(paint.red(f"[failed] {label} ({resp.status}): {resp.error_reason or str(resp.data)[:300]}"))


def print_offer_events(events: list[dict], indent: str = "   ") -> None:
    for ev in events:
        when = ""
        if ev["ts"]:
            when = datetime.datetime.fromtimestamp(ev["ts"] / 1000).strftime("%m-%d %H:%M")
        amount = f"{ev['amount']:.2f}€" if ev["amount"] is not None else ""
        oid = f"  offer={ev['offer_id']}" if ev["offer_id"] else ""
        print(f"   {when}  {ev['kind']:<15} {amount}{oid}")
        print(f"       {str(ev['text'])[:90]}")


def offer_label(kind: str) -> str:
    """Colored one-line state label for an offer ``kind`` (from models)."""
    labels = {
        "seller-counter": paint.yellow("esperando tu respuesta"),
        "our-offer": paint.dim("esperando la respuesta del vendedor"),
        "our-counter": paint.dim("esperando la respuesta del vendedor"),
        "seller-accepted": paint.green("aceptada por el vendedor (comprar en 24h)"),
        "accepted-buy": paint.green("aceptada por ti — comprar en 24h"),
        "seller-declined": paint.red("rechazada por el vendedor"),
        "our-declined": paint.red("rechazada por ti"),
        "expired": paint.dim("caducada"),
    }
    return labels.get(kind, kind)


def offer_tag(kind: str) -> str:
    """Short colored badge for scanning dashboards (``[ACEPTADA]`` etc.)."""
    tags = {
        "accepted-buy": paint.green("[ACEPTADA]"),
        "seller-accepted": paint.green("[ACEPTADA]"),
        "seller-counter": paint.bold(paint.yellow("[PENDIENTE]")),
        "our-offer": paint.cyan("[EN ESPERA]"),
        "our-counter": paint.cyan("[EN ESPERA]"),
        "seller-declined": paint.red("[RECHAZADA]"),
        "our-declined": paint.red("[RECHAZADA]"),
        "expired": paint.dim("[CADUCADA]"),
    }
    return tags.get(kind, "")


def msg_type_label(m: dict) -> str:
    t = m.get("type") or "text"
    if t == "text":
        return ""
    if t == "delivery_generic":
        return paint.yellow(" [offer]")
    if t == "prompt_buyer_to_sell":
        return paint.dim(" [prompt-buy]")
    if t == "review":
        return paint.dim(" [review]")
    if t == "delivery_transaction_claim_period_started":
        return paint.dim(" [claim]")
    return paint.dim(f" [{t}]")


def fmt_thread_message(m: dict) -> str:
    if m.get("from_self"):
        mark = paint.green("->")
    else:
        mark = paint.cyan("<-")
    stamp = m.get("timestamp")
    when = (
        datetime.datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d %H:%M:%S") if stamp else ""
    )
    status = m.get("status") or ""
    st = {"read": paint.dim("·read"), "received": paint.dim("·recvd"), "sent": paint.dim("·sent")}.get(
        status, paint.dim(f"·{status}")
    )
    toks = m.get("time_token") or ""
    head = f"  {mark} {when} {st}"
    if m.get("type") not in (None, "", "text"):
        head += msg_type_label(m)
    if toks:
        head += paint.dim(f" t={toks}")
    return head + "\n" + "      " + str(m.get("text") or offer_payload_summary(m.get("payload")))
