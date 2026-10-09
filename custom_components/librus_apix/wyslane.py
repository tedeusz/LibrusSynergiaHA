"""Wiadomosci wyslane: tolerancyjny parser listy i tresci (biblioteka librus-apix zna dobrze tylko skrzynke odbiorcza).

Modul nie zalezy od Home Assistanta. Adres wiadomosci bierzemy z PRAWDZIWEGO linku w wierszu listy
(a nie skladamy go z identyfikatora), a tresc szukamy kilkoma sposobami.
"""
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag

_LINK = re.compile(r"/wiadomosci/\d+/\d+/\d+")


def _tekst(tag: Any) -> str:
    return " ".join(tag.get_text(" ").split()) if tag is not None else ""


def parsuj_liste(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Wiersze tabeli wyslanych -> [{author (odbiorca), title, date, href (sciezka), has_attachment}]."""
    tabela = soup.find("table", attrs={"class": "decorated stretch"})
    if tabela is None:
        raise ValueError("Nie znaleziono tabeli wiadomosci")
    wiersze = tabela.find_all("tr", attrs={"class": ["line0", "line1"]})
    if not wiersze or "Brak wiadomości" in wiersze[0].get_text():
        return []
    wynik = []
    for tr in wiersze:
        td = tr.find_all("td")
        if len(td) < 5:
            continue
        link = next((a for a in tr.find_all("a", href=True) if _LINK.search(a["href"])), None)
        wynik.append({
            "author": _tekst(td[2]),
            "title": _tekst(td[3]),
            "date": _tekst(td[4]),
            "href": link["href"] if link else "",
            "has_attachment": td[1].find("img") is not None,
        })
    return wynik


def adres_tresci(base_url: str, send_url: str, href: str) -> str:
    """Pelny adres wiadomosci z linku listy; gdy to sam identyfikator - spod skrzynki nadawczej."""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return base_url.rstrip("/") + href
    return send_url.rstrip("/") + "/" + href


def parsuj_tresc(soup: BeautifulSoup) -> Optional[str]:
    """Tresc wiadomosci: znany kontener, potem dowolny element z 'message' w klasie, na koncu caly blok strony."""
    kandydaci: List[Optional[Tag]] = [soup.find("div", attrs={"class": "container-message-content"})]
    kandydaci += soup.find_all(lambda t: t.name == "div" and any("message-content" in c for c in (t.get("class") or [])))
    kandydaci.append(soup.find("div", attrs={"class": "container-background"}))
    for k in kandydaci:
        tekst = k.get_text("\n").strip() if k is not None else ""
        if tekst:
            return tekst
    return None
