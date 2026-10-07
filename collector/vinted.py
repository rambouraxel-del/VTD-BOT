"""Lecture d'une page de recherche Vinted (port minimal de addictcode/vinted-telegram-bot).

Vinted a retiré l'API JSON /api/v2/catalog/items (404). Les annonces sont
désormais intégrées dans la page catalogue (payload Next.js « flight »).
Fonctionnement, identique au projet d'origine :
  1. session anonyme : visite de la page d'accueil, cookies conservés ;
  2. lecture de la page catalogue de la recherche (tri « plus récentes ») ;
  3. extraction du tableau d'annonces du payload.

Volontairement NON repris : rotation de proxies et de User-Agent, navigateur
headless, contournement de challenge. Un blocage est signalé (VintedBlocked)
et le collector ralentit ; il n'essaie jamais de le contourner.
Python standard uniquement.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

TIMEOUT = 20
SESSION_TTL = 20 * 60
FLIGHT_MARKER = 'self.__next_f.push([1,"'
ITEMS_MARKER = '"items":{"items":['
CHALLENGE_HINTS = ("captcha-delivery.com", "just a moment", "you have been blocked",
                   "access denied", "attention required")


class VintedError(Exception):
    """Erreur réseau / HTTP / format : on passe à la recherche suivante."""


class VintedBlocked(VintedError):
    """Refus ou challenge anti-bot : on ralentit, on ne contourne pas."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # redirections suivies à la main (cookies)
        return None


class VintedClient:
    def __init__(self, user_agent: str, accept_language: str = "fr-FR,fr;q=0.9") -> None:
        self.user_agent = user_agent
        self.accept_language = accept_language
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._sessions: dict[str, tuple[dict[str, str], float]] = {}

    # ------------------------------------------------------------------ HTTP
    def _get(self, url: str, headers: dict[str, str]) -> tuple[int, Any, str]:
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self.accept_language,
            "Accept-Encoding": "gzip",
            **headers,
        })
        try:
            res = self._opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as err:  # 3xx/4xx/5xx : on lit quand même
            res = err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise VintedError(f"réseau : {err}") from err
        raw = res.read() if hasattr(res, "read") else b""
        if (res.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return res.status if hasattr(res, "status") else res.code, res.headers, raw.decode("utf-8", "replace")

    def _session(self, host: str, refresh: bool = False) -> dict[str, str]:
        cached = self._sessions.get(host)
        if cached and not refresh and time.time() - cached[1] < SESSION_TTL:
            return cached[0]
        jar: dict[str, str] = {}
        url = f"https://{host}/"
        for _ in range(6):  # le cookie de session est posé sur une redirection intermédiaire
            status, headers, _ = self._get(url, {"Cookie": _cookie_header(jar)} if jar else {})
            _merge_set_cookies(jar, headers.get_all("Set-Cookie") or [])
            location = headers.get("Location")
            if 300 <= status < 400 and location:
                url = f"https://{host}{location}" if location.startswith("/") else location
                continue
            if status in (403, 429):
                raise VintedBlocked(f"page d'accueil : HTTP {status}")
            break
        self._sessions[host] = (jar, time.time())
        return jar

    # --------------------------------------------------------------- catalog
    def fetch_catalog(self, search_url: str) -> list[dict[str, Any]]:
        parts = urlsplit(search_url.strip())
        host = parts.hostname or ""
        if parts.scheme != "https" or "vinted." not in host or not parts.path.startswith("/catalog"):
            raise VintedError(f"URL de recherche Vinted invalide : {search_url}")
        page = f"https://{host}/catalog?{catalog_query(parts.query)}&_rsc"
        for attempt in (1, 2):
            cookies = self._session(host, refresh=attempt == 2)
            status, _, body = self._get(page, {
                "RSC": "1",
                "Referer": f"https://{host}/catalog",
                "Cookie": _cookie_header(cookies),
            })
            if status in (401, 403) and attempt == 1:
                continue  # session expirée : une seule nouvelle session, puis on abandonne
            if status in (401, 403, 429):
                raise VintedBlocked(f"catalogue : HTTP {status}")
            if status != 200:
                raise VintedError(f"catalogue : HTTP {status}")
            return parse_catalog(body, host)
        raise VintedBlocked("catalogue : accès refusé")


def catalog_query(raw_query: str) -> str:
    """Filtres de l'utilisateur conservés tels quels, tri forcé sur « plus récentes »."""
    keep = [p for p in raw_query.split("&")
            if p and p.split("=", 1)[0] not in ("time", "page", "per_page", "order")]
    return "&".join(keep + ["order=newest_first"])


def parse_catalog(body: str, host: str) -> list[dict[str, Any]]:
    flight = _flight_payload(body) if FLIGHT_MARKER in body else body
    at = flight.find(ITEMS_MARKER)
    if at < 0:
        lowered = body[:20000].lower()
        if any(h in lowered for h in CHALLENGE_HINTS):
            raise VintedBlocked("page de challenge anti-bot reçue")
        raise VintedError("format de page inattendu (aucune liste d'annonces)")
    items, _ = json.JSONDecoder().raw_decode(flight, flight.index("[", at))
    out = []
    for el in items:
        p = (el or {}).get("productItem") or {}
        if p.get("isPromoted"):
            continue  # annonces sponsorisées
        item_id = _text(p.get("id"))
        if not item_id:
            continue
        url = _text(p.get("url"))
        if url and url.startswith("/"):
            url = f"https://{host}{url}"
        price_info = p.get("price") or {}
        try:
            price = float(_text(price_info.get("amount")) or "")
        except ValueError:
            price = None
        photos = p.get("photos") or []
        photo = _text(photos[0].get("url")) if photos and isinstance(photos[0], dict) else None
        box = p.get("itemBox") or {}
        size = condition = None
        second = _text(box.get("secondLine"))  # « taille · état », ou juste l'état
        if second:
            chunks = [c.strip() for c in second.split(" · ")]
            condition = chunks[-1]
            size = chunks[0] if len(chunks) > 1 else None
        out.append({
            "id": item_id,
            "url": url,
            "title": _text(p.get("title")),
            "price": price,
            "currency": _text(price_info.get("currencyCode")),
            "brand": _text(box.get("firstLine")),
            "size": size,
            "condition": condition,
            "photo": photo or _text(p.get("thumbnailUrl")),
        })
    return out


def _flight_payload(html: str) -> str:
    """Concatène les morceaux `self.__next_f.push([1,"..."])` (chaînes JSON)."""
    out, i = [], 0
    while (i := html.find(FLIGHT_MARKER, i)) >= 0:
        start = i + len(FLIGHT_MARKER) - 1  # guillemet ouvrant
        j = start + 1
        while j < len(html):
            c = html[j]
            if c == "\\":
                j += 2
            elif c == '"':
                break
            else:
                j += 1
        if j >= len(html):
            break
        out.append(json.loads(html[start:j + 1]))
        i = j + 1
    return "".join(out)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    t = str(value).strip()
    return None if not t or t == "$undefined" else t


def _merge_set_cookies(jar: dict[str, str], lines: list[str]) -> None:
    for line in lines:
        name, _, value = line.split(";", 1)[0].partition("=")
        name, value = name.strip(), value.strip()
        if not name or (not value and jar.get(name)):
            continue  # Vinted envoie aussi un cookie vide « d'effacement » : on garde la vraie valeur
        jar[name] = value


def _cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items() if v)
