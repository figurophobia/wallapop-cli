"""Wallapop API constants, paths and local credential/session storage."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile

API_BASE = "https://api.wallapop.com/api/v3"


API_ROOT = "https://api.wallapop.com"  # host root; bff endpoints live under /bff (no /api/v3)


WEB_BASE = "https://es.wallapop.com"


PUB_KEY = "pub-c-255dc549-86f5-4abd-8b9e-921d5a02fde7"


SUB_KEY = "sub-c-89405e27-d4df-4d87-aca1-d6e9118f0a0d"


PUB_ORIGIN = "ps7.pndsn.com"


PNSDK = "PubNub-JS-Web/10.2.6"


WEB_APP_VERSION = "8.2656.0"


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "wallapop_cli")


CRED_PATH = os.path.join(CONFIG_DIR, "credentials.json")


SESSION_PATH = os.path.join(CONFIG_DIR, "session.json")


CONTACTED_PATH = os.path.join(CONFIG_DIR, "contacted.json")


CLIENT_ID = "wallapop"


VERSION = "0.1.0"


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


CITIES = {
    "espana": (40.0, -3.5),
    "spain": (40.0, -3.5),
    "espana-norte": (42.8, -2.9),
    "espana-sur": (37.2, -4.1),
    "espana-este": (39.6, -0.3),
    "espana-oeste": (39.8, -6.0),
    "espana-noreste": (41.9, 2.0),
    "espana-noroeste": (43.3, -7.0),
    "centro": (40.4, -3.7),
    "sur": (37.2, -4.1),
    "norte": (42.8, -2.9),
    "noreste": (41.9, 2.0),
    "noroeste": (43.3, -7.0),
    "este": (39.6, -0.3),
    "oeste": (39.8, -6.0),
    "galicia": (42.6, -8.1),
    "asturias": (43.4, -5.9),
    "cantabria": (43.4, -3.9),
    "pais-vasco": (42.85, -2.68),
    "navarra": (42.7, -1.65),
    "la-rioja": (42.47, -2.44),
    "aragon": (41.65, -0.88),
    "cataluna": (41.83, 1.53),
    "castilla-y-leon": (41.76, -4.77),
    "madrid": (40.4168, -3.7038),
    "castilla-la-mancha": (39.86, -3.56),
    "extremadura": (39.47, -6.37),
    "valencia": (39.4699, -0.3763),
    "comunidad-valenciana": (39.6, -0.3),
    "murcia": (37.99, -1.13),
    "andalucia": (37.2, -4.1),
    "sevilla": (37.3891, -5.9845),
    "malaga": (36.7213, -4.4214),
    "bilbao": (43.2630, -2.9350),
    "barcelona": (41.3896, 2.1476),
    "zaragoza": (41.6488, -0.8891),
    "granada": (37.1773, -3.5986),
    "portugal": (38.7, -9.1),
    "lisboa": (38.72, -9.14),
    "porto": (41.15, -8.61),
    "italia": (41.9, 12.5),
    "roma": (41.9, 12.5),
    "milan": (45.46, 9.19),
    "napoles": (40.85, 14.27),
    "francia": (48.85, 2.35),
    "paris": (48.85, 2.35),
    "lyon": (45.76, 4.83),
    "marruecos": (34.02, -6.8),
    "casablanca": (33.57, -7.61),
    "andorra": (42.5, 1.6),
    "reino-unido": (51.5, -0.12),
    "londres": (51.5, -0.12),
    "alemania": (52.5, 13.4),
    "berlin": (52.52, 13.4),
    "belgica": (50.85, 4.35),
    "bruselas": (50.85, 4.35),
}


DEFAULT_GEO = CITIES["espana"]


CATEGORIES = {
    100: "Coches",
    200: "Inmobiliaria",
    14000: "Motos",
    12465: "Moda y accesorios",
    12545: "Tecnologia y electronica",
    16000: "Moviles y telefonia",
    15000: "Informatica",
    12579: "Deporte y ocio",
    17000: "Bicicletas",
    12900: "Consolas y videojuegos",
    12467: "Hogar y jardin",
    13100: "Electrodomesticos",
    12463: "Cine, libros y musica",
    12461: "Ninos y bebes",
    18000: "Coleccionismo",
    19000: "Construccion y reformas",
    20000: "Industria y agricultura",
    21000: "Empleo",
    13200: "Servicios",
}


ORDER_ALIASES = {
    "relevance": "most_relevance",
    "most_relevance": "most_relevance",
    "newest": "newest",
    "price_asc": "price_low_to_high",
    "price_low_to_high": "price_low_to_high",
    "price_desc": "price_high_to_low",
    "price_high_to_low": "price_high_to_low",
    "closest": "closest",
}


def ensure_config_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str, data: Any, private: bool = True) -> None:
    ensure_config_dir()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    if private:
        os.chmod(path, 0o600)


def load_credentials() -> dict:
    return load_json(CRED_PATH, {})


def save_credentials(creds: dict) -> None:
    save_json(CRED_PATH, creds)


def clear_credentials() -> None:
    try:
        os.remove(CRED_PATH)
    except OSError:
        pass


def load_session() -> dict:
    return load_json(SESSION_PATH, {})


def save_session(session: dict) -> None:
    save_json(SESSION_PATH, session)


def clear_session() -> None:
    try:
        os.remove(SESSION_PATH)
    except OSError:
        pass


def parse_cookie_string(raw: str) -> dict:
    result: dict[str, str] = {}
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        result[key.strip()] = value.strip()
    return result


WALLAPOP_COOKIE_KEYS = ("accessToken", "__Secure-next-auth.session-token", "device_id", "device_fgp")


def read_firefox_cookies() -> dict:
    profile_dir = os.path.expanduser("~/.mozilla/firefox")
    db_path = None
    best_mtime = -1
    try:
        for entry in os.scandir(profile_dir):
            if not entry.is_dir():
                continue
            candidate = os.path.join(entry.path, "cookies.sqlite")
            if os.path.isfile(candidate) and os.path.getmtime(candidate) > best_mtime:
                best_mtime = os.path.getmtime(candidate)
                db_path = candidate
    except OSError:
        return {}
    if not db_path:
        return {}
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        shutil.copy2(db_path, tmp)
    except OSError:
        os.remove(tmp)
        return {}
    found: dict[str, str] = {}
    try:
        with sqlite3.connect(tmp, timeout=5) as con:
            rows = con.execute(
                "select host, name, value from moz_cookies where host like '%wallapop.com'"
            ).fetchall()
    except (sqlite3.Error, OSError):
        rows = []
    finally:
        os.remove(tmp)
    for host, name, value in rows:
        if name in WALLAPOP_COOKIE_KEYS and value:
            found[name] = value
    return found


def load_contacted() -> set[str]:
    return set(load_json(CONTACTED_PATH, []))


def save_contacted(ids: set[str]) -> None:
    save_json(CONTACTED_PATH, sorted(ids), private=False)
