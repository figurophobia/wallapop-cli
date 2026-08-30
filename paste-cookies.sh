#!/usr/bin/env bash
# paste-cookies.sh — inicia sesión en Wallapop pegando UNA request del navegador.
#
#   1. Abre https://es.wallapop.com logueado  ->  F12  ->  pestaña Red (Network).
#   2. Busca cualquier petición de wallapop (es.wallapop.com o api.wallapop.com)
#      y haz clic derecho -> Copy -> Copy as cURL (bash).
#   3. Pega el "curl ..." completo en cookies.txt  (o cópialo al portapapeles).
#   4. Ejecuta:
#        ./paste-cookies.sh          (usa cookies.txt)
#        ./paste-cookies.sh --clip   (usa el portapapeles)
#
# El script saca de la request la cabecera 'Cookie:' y hace login con ella.
set -euo pipefail

SRC="cookies.txt"
FORCE_CLIP=0
case "${1:-}" in
  --clip) FORCE_CLIP=1; SRC="" ;;
  "") ;;
  *) SRC="$1" ;;
esac

RAW=""
if [ "$FORCE_CLIP" = "1" ]; then
  CLIP="$( { wl-paste 2>/dev/null || true; xclip -o -selection clipboard 2>/dev/null || true; pbpaste 2>/dev/null || true; } )"
  if [ -z "$CLIP" ]; then echo "error: portapapeles vacío (copia la request primero)" >&2; exit 2; fi
  RAW="$CLIP"
elif [ -s "$SRC" ]; then
  RAW="$(cat "$SRC")"
else
  echo "error: no hay $SRC ni --clip. Pega la request (Copy as cURL) en cookies.txt" >&2
  exit 2
fi

COOKIES="$(printf '%s' "$RAW" | grep -v '^#' | tr '\n' ' ')"

# Si lo pegado es un curl, extrae la cabecera 'Cookie:' de sus -H.
if printf '%s' "$COOKIES" | grep -q '^curl '; then
  COOKIES="$(printf '%s' "$COOKIES" | python3 -c '
import re, sys
data = sys.stdin.read()
for m in re.finditer(r"-H\s+([\x27\"])(.*?)\1", data):
    key, _, val = m.group(2).partition(":")
    if key.strip().lower() == "cookie":
        print(val.strip())
        break
')"
fi

if [ -z "$COOKIES" ]; then
  echo "error: no encontré la cabecera Cookie en la request." >&2
  echo "       Tiene que ser 'Copy as cURL (bash)' y llevarse -H 'cookie: ...'" >&2
  exit 2
fi

python3 wallapop.py login --cookies "$COOKIES"