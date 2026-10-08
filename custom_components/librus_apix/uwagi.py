"""Parsery stron "Uwagi" i "Zachowanie" Librus Synergia (bez zaleznosci od Home Assistanta).

Uwagi (https://synergia.librus.pl/uwagi) strona moze miec dwa uklady, wiec parser rozpoznaje oba:
  1. jedna tabela z wierszem naglowkowym (kolumny w dowolnej kolejnosci),
  2. osobna tabelka na kazda uwage, wiersze "Etykieta | Wartosc".

Zachowanie to wiersz "Zachowanie" na stronie ocen (ta strona jest i tak pobierana do ocen,
wiec zachowanie nie kosztuje dodatkowego zapytania): oceny semestralne i roczna oraz wpisy
pozytywne/negatywne (grade-box z opisem w atrybucie title).

Podejscie do rozpoznawania ukladu (mapowanie naglowkow) i struktury wiersza zachowania
podejrzano w forkach mchuc i TomaszSyc; kod jest napisany od nowa.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

UWAGI_URL = "https://synergia.librus.pl/uwagi"

_POLA = ("data", "nauczyciel", "rodzaj", "kategoria", "tresc")

# etykieta/naglowek (po normalizacji) zawiera jedno z tych slow -> pole
_KOLUMNY = {
    "data": ("data", "dodano", "termin"),
    "nauczyciel": ("nauczyciel", "dodal", "dodala", "wystawil", "autor"),
    "rodzaj": ("rodzaj", "typ"),
    "kategoria": ("kategoria",),
    "tresc": ("tresc", "uwaga", "opis", "komentarz"),
}

_ZAMIANA = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def _norm(tekst: str) -> str:
    """Male litery, bez polskich znakow i bez nadmiarowych spacji (do porownywania etykiet)."""
    return " ".join(tekst.lower().translate(_ZAMIANA).split())


def _tekst(el: Tag) -> str:
    return " ".join(el.get_text(" ", strip=True).replace("\xa0", " ").split())


def _pole(etykieta: str) -> Optional[str]:
    n = _norm(etykieta).rstrip(":").strip()
    for pole, warianty in _KOLUMNY.items():
        if any(w in n for w in warianty):
            return pole
    return None


def _pusta_uwaga() -> Dict[str, str]:
    return {p: "" for p in _POLA}


def _z_naglowkiem(tabela: Tag) -> List[Dict[str, str]]:
    """Uklad 1: tabela z wierszem naglowkowym."""
    wiersze = tabela.find_all("tr")
    naglowki: List[Optional[str]] = []
    start = 0
    for i, wiersz in enumerate(wiersze):
        if i != 0 and wiersz.find_parent("thead") is None:
            continue
        komorki = wiersz.find_all(["th", "td"], recursive=False)
        pola = [_pole(_tekst(k)) for k in komorki]
        if sum(1 for p in pola if p) >= 2:
            naglowki, start = pola, i + 1
        break
    if not naglowki:
        return []
    wynik = []
    for wiersz in wiersze[start:]:
        komorki = wiersz.find_all("td", recursive=False)
        if len(komorki) < 2:
            continue
        uwaga = _pusta_uwaga()
        for pole, komorka in zip(naglowki, komorki):
            if pole:
                uwaga[pole] = _tekst(komorka)
        if uwaga["tresc"] or uwaga["data"]:
            wynik.append(uwaga)
    return wynik


def _etykieta_wartosc(tabela: Tag) -> List[Dict[str, str]]:
    """Uklad 2: wiersze "Etykieta | Wartosc" (etykieta w <th> albo w pierwszym <td>)."""
    uwaga = _pusta_uwaga()
    trafienia = 0
    for wiersz in tabela.find_all("tr"):
        komorki = wiersz.find_all(["th", "td"], recursive=False)
        if len(komorki) != 2:
            continue
        pole = _pole(_tekst(komorki[0]))
        if pole and not uwaga[pole]:
            uwaga[pole] = _tekst(komorki[1])
            trafienia += 1
    if trafienia >= 2 and (uwaga["tresc"] or uwaga["data"]):
        return [uwaga]
    return []


def _parsuj_date(tekst: str) -> Optional[date]:
    tekst = (tekst or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(tekst, fmt).date()
        except ValueError:
            continue
    return None


def _znak(rodzaj: str, kategoria: str = "") -> str:
    """pozytywna / negatywna / neutralna wg rodzaju (a gdy pusty - kategorii) wpisu."""
    n = _norm(f"{rodzaj} {kategoria}")
    if "pozyt" in n or "pochwal" in n:
        return "pozytywna"
    if "negat" in n or "nagan" in n:
        return "negatywna"
    return "neutralna"


def _id(krotka: Tuple[str, ...], numer: int) -> str:
    surowe = "|".join(krotka) + f"|{numer}"
    return hashlib.md5(surowe.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def parsuj_uwagi(html: str) -> Tuple[List[Dict[str, Any]], bool]:
    """HTML strony /uwagi -> (uwagi od najnowszej, nierozpoznany_uklad).

    Kazda uwaga: id (stabilne miedzy odswiezeniami), data, nauczyciel, rodzaj, kategoria,
    tresc oraz znak (pozytywna / negatywna / neutralna). Flaga nierozpoznany_uklad jest True,
    gdy na stronie nie ma napisu "Brak uwag", a nie znaleziono zadnej uwagi - czyli
    prawdopodobnie zmienil sie uklad strony (wtedy nie wolno traktowac wyniku jak "brak uwag").
    """
    soup = BeautifulSoup(html or "", "lxml")
    tabele = soup.select("table.decorated") or soup.find_all("table")
    uwagi: List[Dict[str, Any]] = []
    for tabela in tabele:
        if tabela.find_parent("table") is not None:
            continue
        uwagi.extend(_z_naglowkiem(tabela) or _etykieta_wartosc(tabela))

    if not uwagi:
        return [], "brak uwag" not in _norm(soup.get_text(" "))

    wystapienia: Dict[Tuple[str, ...], int] = {}
    for uwaga in uwagi:
        krotka = tuple(uwaga[p] for p in _POLA)
        numer = wystapienia.get(krotka, 0)
        wystapienia[krotka] = numer + 1
        uwaga["id"] = _id(krotka, numer)
        uwaga["znak"] = _znak(uwaga["rodzaj"], uwaga["kategoria"])

    # od najnowszej; wpisy bez daty na koncu; kolejnosc stabilna (sorted jest stabilne)
    uporzadkowane = sorted(
        uwagi,
        key=lambda u: (lambda d: (d is None, -d.toordinal() if d else 0))(_parsuj_date(u["data"])),
    )
    return uporzadkowane, False


# --- Zachowanie -------------------------------------------------------------------


def puste_zachowanie() -> Dict[str, Any]:
    """Struktura zachowania bez danych (nie znaleziono wiersza "Zachowanie")."""
    return {
        "znaleziono": False,
        "okres_1": {"ocena": None, "propozycja": False},
        "okres_2": {"ocena": None, "propozycja": False},
        "roczna": {"ocena": None, "propozycja": False},
        "wpisy": [],
    }


def _ocena_lub_none(tekst: str) -> Optional[str]:
    tekst = (tekst or "").strip()
    return None if tekst in ("", "-") else tekst


def _pola_z_title(title: str) -> Dict[str, str]:
    """"Data wystawienia: ...<br>Dodal: ...<br>Komentarz: ..." -> {znormalizowana etykieta: wartosc}.

    Linie bez dwukropka dopisuja sie do poprzedniej wartosci (wieloliniowy komentarz).
    """
    pola: Dict[str, str] = {}
    ostatnia = None
    for linia in BeautifulSoup(title or "", "lxml").get_text("\n").split("\n"):
        etykieta, dwukropek, wartosc = linia.partition(":")
        if not dwukropek:
            if ostatnia and linia.strip():
                pola[ostatnia] = f"{pola[ostatnia]} {linia.strip()}".strip()
            continue
        ostatnia = _norm(etykieta)
        pola[ostatnia] = wartosc.strip()
    return pola


def _wpisy_zachowania(komorka: Tag, okres: int) -> List[Dict[str, Any]]:
    wynik = []
    for a in komorka.select("span.grade-box > a"):
        klasy = a.parent.get("class", [])
        rodzaj = (
            "pozytywne" if "positive-behaviour" in klasy
            else "negatywne" if "negative-behaviour" in klasy
            else "neutralne"
        )
        pola = _pola_z_title(a.get("title", ""))
        data = pola.get("data wystawienia", "")
        wynik.append({
            "okres": okres,
            "ocena": _tekst(a),
            "rodzaj": rodzaj,
            "data": data.split(" ")[0] if data else "",
            "nauczyciel": pola.get("dodal", ""),
            "komentarz": pola.get("komentarz", ""),
        })
    wystapienia: Dict[Tuple[str, ...], int] = {}
    for w in wynik:
        krotka = (str(w["okres"]), w["ocena"], w["rodzaj"], w["data"], w["nauczyciel"], w["komentarz"])
        numer = wystapienia.get(krotka, 0)
        wystapienia[krotka] = numer + 1
        w["id"] = _id(krotka, numer)
    return wynik


def parsuj_zachowanie(html: str) -> Dict[str, Any]:
    """Wiersz "Zachowanie" ze strony ocen -> oceny (okres_1, okres_2, roczna) i wpisy.

    Kolumny za etykieta "Zachowanie": wpisy okresu 1, ocena okresu 1, wpisy okresu 2, ocena roczna.
    Osobnej komorki oceny okresu 2 nie ma - jesli szczegoly (przedmioty_zachowanie) podaja ja
    w wierszu "Ocena srodroczna / przewidywana", uzupelnia sie nia tylko brakujaca wartosc.
    propozycja=True, gdy etykieta zawiera "propon" lub "przewid" (ocena jeszcze nie jest ostateczna).
    """
    soup = BeautifulSoup(html or "", "lxml")
    wynik = puste_zachowanie()

    glowny = None
    indeks = 0
    for tr in soup.find_all("tr"):
        komorki = tr.find_all("td", recursive=False)
        for i, k in enumerate(komorki[:3]):
            if _norm(_tekst(k)) == "zachowanie" and len(komorki) >= i + 3:
                glowny, indeks = tr, i
                break
        if glowny is not None:
            break
    if glowny is None:
        return wynik
    wynik["znaleziono"] = True

    k = glowny.find_all("td", recursive=False)
    wynik["wpisy"] = _wpisy_zachowania(k[indeks + 1], 1)
    wynik["okres_1"]["ocena"] = _ocena_lub_none(_tekst(k[indeks + 2]))
    if len(k) > indeks + 3:
        wynik["wpisy"] += _wpisy_zachowania(k[indeks + 3], 2)
    if len(k) > indeks + 4:
        wynik["roczna"]["ocena"] = _ocena_lub_none(_tekst(k[indeks + 4]))

    szczegoly = glowny.find_next_sibling("tr", id="przedmioty_zachowanie")
    okres = 1
    for tr in szczegoly.find_all("tr") if szczegoly else []:
        komorki = tr.find_all("td", recursive=False)
        if len(komorki) == 1:
            n = _norm(_tekst(komorki[0]))
            if n in ("okres 1", "okres 2"):
                okres = int(n[-1])
            continue
        if len(komorki) < 3 or "right" not in komorki[0].get("class", []):
            continue
        etykieta = _norm(_tekst(komorki[0]))
        wartosc = next((t for t in map(_tekst, komorki[1:]) if t), "")
        if _ocena_lub_none(wartosc) is None:
            continue
        roczna = "roczn" in etykieta and "srodroczn" not in etykieta
        cel = wynik["roczna"] if roczna else wynik[f"okres_{okres}"]
        if cel["ocena"] is None:
            cel["ocena"] = wartosc
            cel["propozycja"] = "propon" in etykieta or "przewid" in etykieta
    return wynik


def pobierz_uwagi(client: Any) -> Tuple[List[Dict[str, Any]], bool, str]:
    """Pobierz i sparsuj strone uwag: (uwagi, nierozpoznany_uklad, html). Blokujace - wolac w executorze.

    Rzuca TokenError (z librus_apix), gdy sesja wygasla albo konto nie ma dostepu do modulu.
    Surowy HTML wraca do diagnostyki (usluga diagnostyka_ocen zapisuje go na serwerze uzytkownika).
    """
    from librus_apix.helpers import no_access_check

    html = client.get(UWAGI_URL).text
    no_access_check(BeautifulSoup(html, "lxml"))
    uwagi, nierozpoznany = parsuj_uwagi(html)
    return uwagi, nierozpoznany, html
