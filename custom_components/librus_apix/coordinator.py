"""Wspolna logika integracji Librus APIX: koordynator danych, helpery i baza encji."""
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _jest_nowa(date_str: str) -> bool:
    """Sprawdz czy data miesci sie w ostatnich 24 godzinach (dzis lub wczoraj)."""
    if not date_str:
        return False
    wczoraj = _dzis() - timedelta(days=1)
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()
            return d >= wczoraj
        except ValueError:
            continue
    return False


def _czysty(text: Any) -> str:
    """Zbij biale znaki (Librus zostawia znaki nowej linii i wielokrotne spacje)."""
    return " ".join(str(text or "").split())


def _wiadomosc_id(msg: Dict[str, Any]) -> Any:
    """ID wiadomosci: href; gdy brak (np. wiadomosc systemowa) - nadawca+temat+data."""
    return msg.get("href") or (msg.get("author", ""), msg.get("title", ""), msg.get("date", ""))


def _czysta_tresc(text: Any, limit: int = 2000) -> str:
    """Tresc wiadomosci: zbij spacje w liniach, zostaw akapity, przytnij do `limit` znakow."""
    wynik: List[str] = []
    pusta = False
    for linia in str(text or "").replace("\r", "\n").split("\n"):
        linia = " ".join(linia.split())
        if not linia:
            pusta = True
            continue
        if pusta and wynik:
            wynik.append("")
        pusta = False
        wynik.append(linia)
    tekst = "\n".join(wynik).strip()
    return tekst if len(tekst) <= limit else tekst[: limit - 1].rstrip() + "…"


def _tresc_klucz(msg: Dict[str, Any]) -> str:
    """Klucz tekstowy wiadomosci do zapisu tresci (JSON wymaga kluczy tekstowych)."""
    wid = _wiadomosc_id(msg)
    return wid if isinstance(wid, str) else "|".join(str(x) for x in wid)


def _dzis() -> date:
    """Dzisiejsza data wg strefy czasowej Home Assistanta."""
    return dt_util.now().date()


def _biezacy_semestr(dzien: date) -> int:
    """Semestr 1: wrzesien-styczen, semestr 2: luty-sierpien."""
    return 1 if dzien.month >= 9 or dzien.month == 1 else 2


def _wartosc_oceny(ocena: str) -> Optional[float]:
    """Wartosc liczbowa oceny ("4+" -> 4.5, "3-" -> 2.75); None dla ocen nieliczbowych."""
    try:
        base = float(ocena[0])
    except (ValueError, IndexError, TypeError):
        return None
    if len(ocena) > 1:
        if "+" in ocena:
            base += 0.5
        elif "-" in ocena:
            base -= 0.25
    return base


def _srednia_ocen(oceny: List[Dict]) -> Optional[float]:
    """Srednia arytmetyczna ocen (bez wag)."""
    wartosci = [
        w for w in (_wartosc_oceny(g.get("ocena", "")) for g in oceny) if w is not None
    ]
    return round(sum(wartosci) / len(wartosci), 2) if wartosci else None


def _suma_wag(oceny: List[Dict]) -> int:
    """Suma wag ocen wliczanych do sredniej."""
    return sum(
        (g.get("waga") or 1)
        for g in oceny
        if g.get("liczy_sie", True) is not False
        and _wartosc_oceny(g.get("ocena", "")) is not None
    )


def _srednia_wazona(oceny: List[Dict]) -> Optional[float]:
    """Srednia wazona: pomija oceny z "Licz do sredniej: nie"; brak wagi = waga 1."""
    suma = wagi = 0.0
    for g in oceny:
        if g.get("liczy_sie", True) is False:
            continue
        wartosc = _wartosc_oceny(g.get("ocena", ""))
        if wartosc is None:
            continue
        waga = g.get("waga") or 1
        suma += wartosc * waga
        wagi += waga
    return round(suma / wagi, 2) if wagi else None


def _srednia(oceny: List[Dict]) -> Optional[float]:
    """Srednia uzywana jako stan czujnikow (wazona lub arytmetyczna)."""
    return _srednia_wazona(oceny) if SREDNIA_WAZONA else _srednia_ocen(oceny)


def _liczba(value: Any) -> Optional[float]:
    """"4,50" -> 4.5; "-", puste i 0 (brak sredniej) -> None."""
    if isinstance(value, (int, float)):
        return float(value) or None
    try:
        return float(str(value).strip().replace(",", ".")) or None
    except ValueError:
        return None


def _komentarz_oceny(desc: str) -> str:
    """Wyciagnij pole "Komentarz" z opisu oceny (biblioteka sklada caly tooltip w `desc`)."""
    for linia in (desc or "").splitlines():
        if linia.strip().lower().startswith("komentarz"):
            return linia.split(":", 1)[1].strip() if ":" in linia else ""
    return ""


def _build_srednie_librus(avg: Any) -> Dict[str, Dict[str, Optional[float]]]:
    """{przedmiot: {semestr: gpa}} -> srednie oficjalne z Librusa (0 = roczna)."""
    return {
        przedmiot: {
            "semestr_1": _liczba(sem.get(1)),
            "semestr_2": _liczba(sem.get(2)),
            "roczna": _liczba(sem.get(0)),
        }
        for przedmiot, sem in (avg or {}).items()
    }


# Pobieraj tresc zadan domowych (osobne zapytanie na kazde zadanie z terminem <= 7 dni).
# Wylacz (False), jesli nie chcesz dodatkowych zapytan do Librusa.
POBIERAJ_TRESC_ZADAN = True
MAX_SZCZEGOLOW_NA_ODSWIEZENIE = 10

# Jak czesto sprawdzac TYLKO nowe wiadomosci (1 zapytanie do Librusa), niezaleznie od
# SCAN_INTERVAL (2 h) dla reszty danych. Ustaw None, zeby wylaczyc szybkie sprawdzanie.
WIADOMOSCI_INTERWAL = timedelta(minutes=3)

# Warstwa srodkowa: oceny, frekwencja (nb), zadania, terminarz, ogloszenia - to one wywoluja
# powiadomienia. Kilka zapytan na cykl. None = tylko pelne odswiezenie (SCAN_INTERVAL).
ZDARZENIA_INTERWAL = timedelta(minutes=15)

# Plan lekcji (2 zapytania na cykl) - np. timedelta(minutes=60), jesli zalezy Ci na szybkich
# zastepstwach. None = tylko pelne odswiezenie (SCAN_INTERVAL, 2 h).
PLAN_INTERWAL = None

# Ile wiadomosci pokazuje jeden ekran przegladania (tyle kafelkow ma pulpit Wiadomosci).
ROZMIAR_WIDOKU = 5
# Ile wiadomosci pobierac z jednej strony Librusa przy przegladaniu starszych.
WIADOMOSCI_NA_STRONE = 100

# Tresc wiadomosci. UWAGA: pobranie tresci OTWIERA wiadomosc w Librusie, czyli oznacza ja
# jako przeczytana (nauczyciel widzi status "przeczytana"). Tryb pobierania:
#   "po_otwarciu" (domyslny, bezpieczniejszy) - nic nie jest pobierane w tle; tresc pobiera
#       dopiero klikniecie wiadomosci na liscie (usluga librus_apix.pobierz_tresc), zapisywana na stale.
#   "od_razu" - tresc kazdej NOWEJ wiadomosci (wykrytej po uruchomieniu) jest pobierana w tle,
#       zapisywana i trafia do zdarzenia/powiadomienia (pole "tresc").
TRYB_TRESCI_WIADOMOSCI = "po_otwarciu"
# Ogranicz do nadawcow, ktorych nazwa zawiera jeden z fragmentow (bez wielkosci liter),
# np. ("Kowalska", "Nowak"). Pusta krotka = wszyscy nadawcy.
TRESC_WIADOMOSCI_NADAWCY: Tuple[str, ...] = ()
MAX_TRESCI_NA_CYKL = 5
# Ile tresci trzymac w pamieci HA (najstarsze wypadaja). None = bez limitu, historia na zawsze.
MAX_TRESCI_W_PAMIECI: Optional[int] = None

# Stan czujnikow sredniej: True = srednia wazona (jak w Librusie), False = arytmetyczna.
# Obie wartosci sa zawsze dostepne w atrybutach.
SREDNIA_WAZONA = True

DNI_TYGODNIA = [
    "poniedzialek", "wtorek", "sroda", "czwartek", "piatek", "sobota", "niedziela",
]

SYMBOL_NIEOBECNOSC = "nb"
SYMBOL_USPRAWIEDLIWIONA = "u"
SYMBOL_SPOZNIENIE = "sp"
SYMBOL_ZWOLNIENIE = "zw"
SYMBOL_OBECNOSC = "ob"
# Symbole, o ktorych warto powiadamiac (zdarzenie HA)
SYMBOLE_ALERTU = frozenset({"nb", "sp", "u", "zw"})


def _parse_date(value: Any) -> Optional[date]:
    """Sparsuj date z formatow Librusa ("2026-10-06", "2026-10-06 wtorek", "06.10.2026")."""
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --- Plan lekcji -------------------------------------------------------------


def _build_plan(periods: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Zamien liste Period na slownik {data ISO: [lekcje]} (puste okienka pomijane)."""
    plan: Dict[str, List[Dict[str, Any]]] = {}
    for p in periods or []:
        lekcje = plan.setdefault(p.date, [])
        info = p.info or {}
        if not p.subject and not info:
            continue  # brak lekcji w tym slocie
        zmiana = next(iter(info), "")
        szczegoly = info.get(zmiana) if zmiana else None
        lekcja: Dict[str, Any] = {
            "numer": p.number,
            "przedmiot": p.subject,
            "nauczyciel_sala": p.teacher_and_classroom,
            "od": p.date_from,
            "do": p.date_to,
            "zmiana": zmiana,
            "odwolana": "odwo" in zmiana.lower(),
        }
        if isinstance(szczegoly, dict):
            for zrodlo, cel in (
                ("teacher_swap", "zastepca"),
                ("subject_swap", "przedmiot_zastepczy"),
                ("classroom_swap", "sala_zastepcza"),
            ):
                if szczegoly.get(zrodlo):
                    lekcja[cel] = szczegoly[zrodlo]
        lekcje.append(lekcja)
    for lekcje in plan.values():
        lekcje.sort(key=lambda l: l["numer"])
    return plan


def _podsumowanie_dnia(dzien: date, lekcje: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Atrybuty opisujace jeden dzien planu."""
    aktywne = [l for l in lekcje if not l["odwolana"]]
    return {
        "data": dzien.isoformat(),
        "dzien_tygodnia": DNI_TYGODNIA[dzien.weekday()],
        "liczba_lekcji": len(aktywne),
        "pierwsza_lekcja_od": aktywne[0]["od"] if aktywne else None,
        "ostatnia_lekcja_do": aktywne[-1]["do"] if aktywne else None,
        "liczba_zmian": sum(1 for l in lekcje if l["zmiana"]),
        "odwolane": [
            f"{l['numer']}. {l['przedmiot']}" for l in lekcje if l["odwolana"]
        ],
        "lekcje": lekcje,
    }


def _nastepny_dzien_nauki(plan: Dict[str, List[Dict]], po: date) -> Optional[date]:
    """Najblizszy dzien po `po`, w ktorym jest przynajmniej jedna nieodwolana lekcja."""
    for iso in sorted(plan):
        d = _parse_date(iso)
        if d and d > po and any(not l["odwolana"] for l in plan[iso]):
            return d
    return None


def _poniedzialek_tygodnia_szkolnego(dzis: date) -> date:
    """Poniedzialek biezacego tygodnia; w weekend - poniedzialek nastepnego."""
    baza = dzis if dzis.weekday() < 5 else dzis + timedelta(days=7 - dzis.weekday())
    return baza - timedelta(days=baza.weekday())


# --- Frekwencja --------------------------------------------------------------


def _build_obecnosc(semestry: Any) -> List[Dict[str, Any]]:
    """Splaszcz wpisy frekwencji ([sem1, sem2]) do listy, najnowsze pierwsze.

    Numer semestru bierzemy z pozycji na liscie (a nie z Attendance.semester,
    ktore w librus-apix oznacza kolejnosc sekcji w HTML).
    """
    wynik: List[Dict[str, Any]] = []
    for nr, wpisy in enumerate(semestry or [], start=1):
        for a in wpisy:
            wynik.append({
                "data": a.date,
                "symbol": (a.symbol or "").strip().lower(),
                "typ": a.type,
                "przedmiot": a.subject,
                "godzina_lekcyjna": a.period,
                "nauczyciel": a.teacher,
                "temat": a.topic,
                "wycieczka": a.excursion,
                "semestr": nr,
                "href": a.href,
            })
    wynik.sort(
        key=lambda w: (_parse_date(w["data"]) or date.min, w["godzina_lekcyjna"]),
        reverse=True,
    )
    return wynik


def _build_frekwencja(freq: Any) -> Optional[Dict[str, float]]:
    """Krotka (sem1, sem2, ogolem) z wartosciami 0-1 -> procenty."""
    try:
        pierwszy, drugi, ogolem = freq
        return {
            "semestr_1": round(pierwszy * 100, 2),
            "semestr_2": round(drugi * 100, 2),
            "ogolem": round(ogolem * 100, 2),
        }
    except (TypeError, ValueError):
        return None


def _wpis_kompakt(w: Dict[str, Any]) -> Dict[str, Any]:
    """Krotki opis wpisu frekwencji do atrybutow."""
    return {
        "data": w["data"],
        "symbol": w["symbol"],
        "typ": w["typ"],
        "przedmiot": w["przedmiot"],
        "godzina_lekcyjna": w["godzina_lekcyjna"],
        "nauczyciel": w["nauczyciel"],
        "temat": w["temat"],
    }


# --- Zadania domowe ----------------------------------------------------------


def _tresc_zadania(szczegoly: Optional[Dict[str, str]]) -> Optional[str]:
    """Wyciagnij tresc zadania ze szczegolow (etykieta zawiera "tresc"/"opis")."""
    for klucz, wartosc in (szczegoly or {}).items():
        k = klucz.lower()
        if "treść" in k or "tresc" in k or "opis" in k:
            return wartosc.strip()
    return None


# --- Identyfikatory (do wykrywania nowych wpisow) -------------------------------


def _ocena_id(grade: Dict[str, Any]) -> Any:
    """ID oceny: href z Librusa; fallback uwzglednia kategorie i wage (bez kolizji)."""
    return grade.get("href") or (
        grade["subject"], grade["date"], grade["grade"],
        grade.get("category", ""), grade.get("weight"),
    )


def _zadanie_id(zadanie: Dict[str, Any]) -> Any:
    return zadanie.get("href") or (
        zadanie["przedmiot"], zadanie["termin"], zadanie["kategoria"],
        zadanie.get("lekcja"), zadanie.get("data_zadania"),
    )


def _zdarzenie_id(zdarzenie: Dict[str, Any]) -> Any:
    return (
        zdarzenie["data"], zdarzenie["tytul"], zdarzenie["przedmiot"],
        zdarzenie.get("numer_lekcji"), zdarzenie.get("href"),
    )


# --- Ogloszenia i tematy lekcji ---------------------------------------------------


def _build_ogloszenia(raw: Any) -> List[Dict[str, Any]]:
    """Lista Announcement -> lista slownikow (kolejnosc jak w Librusie)."""
    wynik = []
    for a in raw or []:
        data = (a.date or "").strip()
        d = _parse_date(data)
        wynik.append({
            "tytul": (a.title or "").strip(),
            "autor": (a.author or "").strip(),
            "data": data,
            "data_iso": d.isoformat() if d else "",
            "tresc": (a.description or "").strip(),
            "jest_nowe": _jest_nowa(data),
        })
    return wynik


def _build_tematy(raw: Any) -> List[Dict[str, Any]]:
    """Lista Lesson (zrealizowane lekcje) -> posortowana lista tematow."""
    wynik = []
    for l in raw or []:
        d = _parse_date(l.date)
        try:
            numer: Any = int(str(l.lesson_number).strip())
        except ValueError:
            numer = str(l.lesson_number).strip()
        wynik.append({
            "data": d.isoformat() if d else str(l.date).strip(),
            "numer": numer,
            "przedmiot": (l.subject or "").strip(),
            "nauczyciel": (l.teacher or "").strip(),
            "temat": (l.topic or "").strip(),
            "frekwencja": (l.attendance_symbol or "").strip(),
        })
    wynik.sort(key=lambda t: (t["data"], t["numer"] if isinstance(t["numer"], int) else 99))
    return wynik


def _tematy_wg_dni(tematy: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Grupuj tematy po dniu; pomijaj lekcje bez wpisanego tematu."""
    wg_dni: Dict[str, List[Dict[str, Any]]] = {}
    for t in tematy:
        if t["temat"]:
            wg_dni.setdefault(t["data"], []).append({
                "numer": t["numer"], "przedmiot": t["przedmiot"],
                "temat": t["temat"], "nauczyciel": t["nauczyciel"],
            })
    return wg_dni


# --- Czas lekcji (znaczniki czasu, aktualna lekcja) -------------------------------


def _na_czas(dzien: Any, godzina: Optional[str]) -> Optional[datetime]:
    """(data, "HH:MM") -> datetime ze strefa HA; None gdy nie da sie sparsowac."""
    d = dzien if isinstance(dzien, date) else _parse_date(dzien)
    if d is None or not godzina:
        return None
    try:
        h, m = (int(x) for x in str(godzina).strip().split(":")[:2])
        return datetime(d.year, d.month, d.day, h, m, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    except ValueError:
        return None


def _nazwa_lekcji(lekcja: Dict[str, Any]) -> str:
    return lekcja.get("przedmiot_zastepczy") or lekcja.get("przedmiot", "")


def _granice_dnia(
    lekcje: List[Dict[str, Any]], dzien: date
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """(poczatek pierwszej, koniec ostatniej) nieodwolanej lekcji dnia."""
    starty, konce = [], []
    for l in lekcje:
        if l["odwolana"]:
            continue
        s, e = _na_czas(dzien, l["od"]), _na_czas(dzien, l["do"])
        if s:
            starty.append(s)
        if e:
            konce.append(e)
    return (min(starty) if starty else None, max(konce) if konce else None)


def _aktualna_lekcja(plan: Dict[str, List[Dict]], teraz: datetime) -> Optional[Dict[str, Any]]:
    """Nieodwolana lekcja trwajaca w chwili `teraz` (poczatek wlacznie, koniec wylacznie)."""
    dzien = teraz.date()
    for l in plan.get(dzien.isoformat()) or []:
        if l["odwolana"]:
            continue
        s, e = _na_czas(dzien, l["od"]), _na_czas(dzien, l["do"])
        if s and e and s <= teraz < e:
            return l
    return None


def _nastepna_lekcja(plan: Dict[str, List[Dict]], teraz: datetime) -> Optional[Dict[str, Any]]:
    """Najblizsza nieodwolana lekcja zaczynajaca sie po `teraz` (tego samego dnia)."""
    dzien = teraz.date()
    kandydaci = []
    for l in plan.get(dzien.isoformat()) or []:
        s = _na_czas(dzien, l["od"])
        if not l["odwolana"] and s and s > teraz:
            kandydaci.append((s, l))
    return min(kandydaci, key=lambda x: x[0])[1] if kandydaci else None


EVENT_NOWA_WIADOMOSC = f"{DOMAIN}_nowa_wiadomosc"
EVENT_NOWA_OCENA = f"{DOMAIN}_nowa_ocena"
EVENT_NOWE_ZADANIE = f"{DOMAIN}_nowe_zadanie"
EVENT_NOWE_ZDARZENIE = f"{DOMAIN}_nowe_zdarzenie"
EVENT_NOWA_NIEOBECNOSC = f"{DOMAIN}_nowa_nieobecnosc"
EVENT_NOWE_OGLOSZENIE = f"{DOMAIN}_nowe_ogloszenie"


class LibrusDataUpdateCoordinator(DataUpdateCoordinator):
    """Klasa zarzadzajaca pobieraniem danych z Librus."""

    def __init__(
        self, hass: HomeAssistant, client: Any, config_entry: Optional[ConfigEntry] = None
    ) -> None:
        """Inicjalizacja koordynatora."""
        self.client = client
        self._seen_message_hrefs: set = set()
        self._seen_grade_ids: set = set()
        self._seen_homework_ids: set = set()
        self._seen_schedule_ids: set = set()
        self._seen_attendance_ids: set = set()
        self._api_lock = asyncio.Lock()
        self._tresci: Dict[str, str] = {}
        self._otwarta: Optional[Dict[str, Any]] = None
        # Przegladanie starszych wiadomosci: strona Librusa, przesuniecie na niej i jej tresc.
        # Domyslnie (strona 0, przesuniecie 0) widok jest "zywy" i pochodzi z self.data.
        self._strona = 0
        self._przesuniecie = 0
        self._widok_lista: Optional[List[Dict[str, Any]]] = None
        self._ogl_przesuniecie = 0
        self._tresci_store: Optional[Store] = (
            Store(hass, 1, f"{DOMAIN}_wiadomosci_{config_entry.entry_id}")
            if config_entry is not None
            else None
        )
        self._seen_announcement_ids: set = set()
        self._homework_details: Dict[str, Dict[str, str]] = {}
        self._first_run: bool = True
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            **({"config_entry": config_entry} if config_entry is not None else {}),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Pobierz aktualne dane z API Librus (pelne odswiezenie, wylacznie z szybkim sprawdzaniem)."""
        async with self._api_lock:
            return await self._pobierz_dane()

    async def async_wczytaj_tresci(self) -> None:
        """Wczytaj zapisane tresci wiadomosci (przezywaja restart HA)."""
        if self._tresci_store is None:
            return
        try:
            zapisane = await self._tresci_store.async_load()
            if isinstance(zapisane, dict):
                self._tresci = {str(k): str(v) for k, v in zapisane.items()}
        except Exception as err:
            _LOGGER.warning("Nie udalo sie wczytac zapisanych tresci wiadomosci: %s", err)

    def _zapisz_tresci(self) -> None:
        if MAX_TRESCI_W_PAMIECI:
            while len(self._tresci) > MAX_TRESCI_W_PAMIECI:
                self._tresci.pop(next(iter(self._tresci)))
        if self._tresci_store is not None:
            self._tresci_store.async_delay_save(lambda: dict(self._tresci), 5)

    @staticmethod
    def _nadawca_pasuje(msg: Dict[str, Any]) -> bool:
        if not TRESC_WIADOMOSCI_NADAWCY:
            return True
        autor = msg.get("author", "").lower()
        return any(f.lower() in autor for f in TRESC_WIADOMOSCI_NADAWCY)

    async def _dociagnij_tresci(self, wiadomosci: List[Dict]) -> None:
        """Pobierz tresc NOWYCH wiadomosci (jeszcze niewidzianych); wolac PRZED _fire_events."""
        if TRYB_TRESCI_WIADOMOSCI != "od_razu":
            return
        pobrano, zmiana = 0, False
        for msg in wiadomosci:
            if _wiadomosc_id(msg) in self._seen_message_hrefs:
                continue  # stare wiadomosci nie sa otwierane (oznaczylyby sie jako przeczytane)
            if not msg.get("href") or not self._nadawca_pasuje(msg):
                continue
            klucz = _tresc_klucz(msg)
            if klucz not in self._tresci:
                if pobrano >= MAX_TRESCI_NA_CYKL:
                    break
                pobrano += 1
                try:
                    surowa = await self.client.async_get_message_content(msg["href"])
                except Exception as err:
                    _LOGGER.warning("Pobranie tresci wiadomosci nie powiodlo sie: %s", err)
                    surowa = None
                tresc = _czysta_tresc(surowa)
                if not tresc:
                    continue  # powiadomienie wyjdzie bez tresci
                self._tresci[klucz] = tresc
                zmiana = True
            msg["tresc"] = self._tresci[klucz]
        if zmiana:
            self._zapisz_tresci()

    def widok_ogloszen(self) -> Dict[str, Any]:
        """Ekran ogloszen (ROZMIAR_WIDOKU sztuk) z calej listy; pozycje liczone od 1."""
        lista = (self.data or {}).get("ogloszenia") or []
        przes = min(self._ogl_przesuniecie, max(0, len(lista) - 1))
        czesc = lista[przes : przes + ROZMIAR_WIDOKU]
        return {
            "od": przes + 1 if czesc else 0,
            "do": przes + len(czesc),
            "razem": len(lista),
            "najnowsze": przes == 0,
            "ogloszenia": czesc,
        }

    def async_przegladaj_ogloszenia(self, kierunek: str) -> bool:
        """Przesun ekran ogloszen: "nastepna" (starsze), "poprzednia" (nowsze), "najnowsze".

        Lista ogloszen jest pobierana w calosci przy pelnym odswiezeniu, wiec bez zapytan do Librusa.
        """
        lista = (self.data or {}).get("ogloszenia") or []
        przes = min(self._ogl_przesuniecie, max(0, len(lista) - 1))
        if kierunek == "najnowsze":
            nowe = 0
        elif kierunek == "nastepna":
            nowe = przes + ROZMIAR_WIDOKU
            if nowe >= len(lista):
                return False
        elif kierunek == "poprzednia":
            if przes == 0:
                return False
            nowe = max(0, przes - ROZMIAR_WIDOKU)
        else:
            return False
        self._ogl_przesuniecie = nowe
        self.async_update_listeners()
        return True

    def widok(self) -> List[Dict[str, Any]]:
        """Wiadomosci aktualnego ekranu (ROZMIAR_WIDOKU sztuk): najnowsze albo starsze po przegladaniu."""
        if self._widok_lista is None:
            return ((self.data or {}).get("wiadomosci") or [])[:ROZMIAR_WIDOKU]
        return self._widok_lista[self._przesuniecie : self._przesuniecie + ROZMIAR_WIDOKU]

    def opis_widoku(self) -> Dict[str, Any]:
        """Polozenie ekranu: numer strony Librusa i pozycje (od 1)."""
        n = len(self.widok())
        return {
            "strona": self._strona + 1,
            "od": self._przesuniecie + 1 if n else 0,
            "do": self._przesuniecie + n,
            "najnowsze": self._widok_lista is None,
        }

    async def _pobierz_strone(self, strona: int) -> Optional[List[Dict[str, Any]]]:
        messages = await self.client.async_get_messages(
            count=WIADOMOSCI_NA_STRONE, page=strona
        )
        return None if messages is None else self._build_wiadomosci(messages)

    async def async_przegladaj(self, kierunek: str) -> bool:
        """Przesun ekran o ROZMIAR_WIDOKU: "nastepna" (starsze), "poprzednia" (nowsze), "najnowsze".

        Samo listowanie NIE otwiera wiadomosci (nie zmienia statusu przeczytania).
        """
        async with self._api_lock:
            strona, przes, lista = self._strona, self._przesuniecie, self._widok_lista
            if kierunek == "najnowsze":
                strona, przes, lista = 0, 0, None
            elif kierunek == "nastepna":
                if lista is None:
                    lista = await self._pobierz_strone(strona)
                    if lista is None:
                        return False
                if przes + ROZMIAR_WIDOKU < len(lista):
                    przes += ROZMIAR_WIDOKU
                else:
                    nowa = await self._pobierz_strone(strona + 1)
                    if not nowa or (lista and nowa[0].get("href") == lista[0].get("href")):
                        return False  # to byla ostatnia strona
                    strona, przes, lista = strona + 1, 0, nowa
            elif kierunek == "poprzednia":
                if przes >= ROZMIAR_WIDOKU:
                    przes -= ROZMIAR_WIDOKU
                elif strona > 0:
                    nowa = await self._pobierz_strone(strona - 1)
                    if not nowa:
                        return False
                    strona, lista = strona - 1, nowa
                    przes = ((len(nowa) - 1) // ROZMIAR_WIDOKU) * ROZMIAR_WIDOKU
                else:
                    return False
            else:
                return False
            if strona == 0 and przes == 0:
                lista = None  # wracamy do zywego widoku najnowszych
            self._strona, self._przesuniecie, self._widok_lista = strona, przes, lista
        self.async_update_listeners()
        return True

    async def async_pobierz_tresc(self, indeks: int) -> bool:
        """Pobierz (raz, na stale) i pokaz tresc wiadomosci z pozycji `indeks` na liscie.

        Wolane po kliknieciu wiadomosci na pulpicie. UWAGA: otwiera wiadomosc w Librusie
        (oznacza jako przeczytana). Zapisana juz tresc nie jest pobierana ponownie.
        """
        async with self._api_lock:
            lista = self.widok()
            if not 0 <= indeks < len(lista):
                _LOGGER.warning("Brak wiadomosci na pozycji %s", indeks)
                return False
            msg = lista[indeks]
            klucz = _tresc_klucz(msg)
            if klucz not in self._tresci:
                if not msg.get("href"):
                    return False
                try:
                    surowa = await self.client.async_get_message_content(msg["href"])
                except Exception as err:
                    _LOGGER.warning("Pobranie tresci wiadomosci nie powiodlo sie: %s", err)
                    return False
                tresc = _czysta_tresc(surowa)
                if not tresc:
                    return False
                self._tresci[klucz] = tresc
                self._zapisz_tresci()
            msg["tresc"] = self._tresci[klucz]
            self._otwarta = {
                "nadawca": msg.get("author", ""),
                "temat": msg.get("title", ""),
                "data": msg.get("date", ""),
                "ma_zalacznik": msg.get("has_attachment", False),
                "tresc": msg["tresc"],
            }
        self.async_update_listeners()
        return True

    def async_uruchom_odswiezanie(self, config_entry: ConfigEntry) -> None:
        """Uruchom warstwowe odswiezanie: wiadomosci, zdarzenia (oceny itd.) i opcjonalnie plan.

        Pelne odswiezenie (wszystko) robi sam koordynator co SCAN_INTERVAL.
        """
        for interwal, akcja in (
            (WIADOMOSCI_INTERWAL, self._szybkie_wiadomosci),
            (ZDARZENIA_INTERWAL, self._odswiez_zdarzenia),
            (PLAN_INTERWAL, self._odswiez_plan),
        ):
            if interwal is not None:
                config_entry.async_on_unload(
                    async_track_time_interval(self.hass, akcja, interwal)
                )

    @staticmethod
    def _grupuj_oceny(grades: List[Dict]) -> Dict[str, List[Dict]]:
        """Grupuj oceny wg przedmiotu i oznacz nowe."""
        wynik: Dict[str, List[Dict]] = {}
        for grade in grades:
            wynik.setdefault(grade["subject"], []).append({
                "ocena": grade["grade"],
                "data": grade["date"],
                "kategoria": grade["category"],
                "nauczyciel": grade["teacher"],
                "semestr": grade.get("semester"),
                "waga": grade.get("weight"),
                "liczy_sie": grade.get("counts", True),
                "komentarz": _komentarz_oceny(grade.get("desc", "")),
                "jest_nowa": _jest_nowa(grade["date"]),
            })
        return wynik

    def _zastosuj_czesciowe(self, nowe: Dict[str, Any]) -> None:
        """Zapisz dane z odswiezenia czesciowego (bez async_set_updated_data - ono przesuwa timer)."""
        if nowe != self.data:
            self.data = nowe
            self.async_update_listeners()

    async def _odswiez_zdarzenia(self, _now: Optional[datetime] = None) -> None:
        """Warstwa srodkowa: oceny, frekwencja, zadania, terminarz, ogloszenia + zdarzenia HA."""
        if self.data is None or self._first_run or self._api_lock.locked():
            return
        try:
            async with self._api_lock:
                grades_raw = await self.client.async_get_grades()
                attendance_raw = await self.client.async_get_attendance()
                homework_raw = await self.client.async_get_homework()
                schedule_raw = await self.client.async_get_schedule()
                ogloszenia_raw = await self.client.async_get_announcements()

                nowe = dict(self.data)
                grades = grades_raw["oceny"] if grades_raw else None
                if grades is not None:
                    nowe["oceny"] = grades
                    nowe["oceny_wg_przedmiotu"] = self._grupuj_oceny(grades)
                    nowe["srednie_librus"] = _build_srednie_librus(grades_raw["srednie_librus"])
                if attendance_raw is not None:
                    nowe["obecnosc"] = _build_obecnosc(attendance_raw)
                if homework_raw is not None:
                    nowe["zadania"] = self._build_zadania(homework_raw)
                    await self._dodaj_szczegoly_zadan(nowe)
                if schedule_raw is not None:
                    nowe["terminarz"] = schedule_raw
                if ogloszenia_raw is not None:
                    nowe["ogloszenia"] = _build_ogloszenia(ogloszenia_raw)

            if grades is not None:
                self._fire_events([], grades)
            if homework_raw is not None:
                self._fire_homework_events(nowe["zadania"])
            if schedule_raw is not None:
                self._fire_schedule_events(nowe["terminarz"])
            if attendance_raw is not None:
                self._fire_attendance_events(nowe["obecnosc"])
            if ogloszenia_raw is not None:
                self._fire_ogloszenia_events(nowe["ogloszenia"])
            self._zastosuj_czesciowe(nowe)
        except Exception as err:  # nigdy nie psuj reszty integracji
            _LOGGER.warning("Odswiezanie zdarzen (oceny, zadania itd.) nie powiodlo sie: %s", err)

    async def _odswiez_plan(self, _now: Optional[datetime] = None) -> None:
        """Tylko plan lekcji (2 zapytania)."""
        if self.data is None or self._first_run or self._api_lock.locked():
            return
        try:
            async with self._api_lock:
                plan_raw = await self.client.async_get_timetable()
            if plan_raw is not None:
                self._zastosuj_czesciowe({**self.data, "plan": _build_plan(plan_raw)})
        except Exception as err:
            _LOGGER.warning("Odswiezanie planu lekcji nie powiodlo sie: %s", err)

    async def _szybkie_wiadomosci(self, _now: Optional[datetime] = None) -> None:
        """Pobierz same wiadomosci, wyslij zdarzenia dla nowych i odswiez czujnik."""
        if self.data is None or self._first_run or self._api_lock.locked():
            return  # brak danych bazowych albo trwa pelne odswiezenie
        try:
            async with self._api_lock:
                messages = await self.client.async_get_messages(count=10)
                if messages is None:
                    return
                wiadomosci = self._build_wiadomosci(messages)
                await self._dociagnij_tresci(wiadomosci)
            self._fire_events(wiadomosci, [])
            if wiadomosci != self.data.get("wiadomosci"):
                # Bez async_set_updated_data: ono przesuwa termin pelnego odswiezenia.
                self.data = {**self.data, "wiadomosci": wiadomosci}
                self.async_update_listeners()
        except Exception as err:  # szybkie sprawdzanie nigdy nie moze psuc reszty integracji
            _LOGGER.warning("Szybkie sprawdzanie wiadomosci nie powiodlo sie: %s", err)

    async def _pobierz_dane(self) -> Dict[str, Any]:
        """Pobierz aktualne dane z API Librus."""
        current_sem = _biezacy_semestr(_dzis())

        try:
            student_info = await self.client.async_get_student_information()
            grades_raw = await self.client.async_get_grades()
            grades = grades_raw["oceny"] if grades_raw else None
            messages = await self.client.async_get_messages(count=10)
            homework_raw = await self.client.async_get_homework()
            schedule_raw = await self.client.async_get_schedule()
            plan_raw = await self.client.async_get_timetable()
            attendance_raw = await self.client.async_get_attendance()
            frequency_raw = await self.client.async_get_attendance_frequency()
            ogloszenia_raw = await self.client.async_get_announcements()
            tematy_raw = await self.client.async_get_completed_lessons()

            # Dane opcjonalne: przy bledzie pobierania zostaja poprzednie
            prev = self.data or {}
            dodatkowe: Dict[str, Any] = {
                "plan": (
                    _build_plan(plan_raw) if plan_raw is not None else prev.get("plan", {})
                ),
                "obecnosc": (
                    _build_obecnosc(attendance_raw)
                    if attendance_raw is not None
                    else prev.get("obecnosc", [])
                ),
                "frekwencja": (
                    _build_frekwencja(frequency_raw)
                    if frequency_raw is not None
                    else prev.get("frekwencja")
                ),
                "ogloszenia": (
                    _build_ogloszenia(ogloszenia_raw)
                    if ogloszenia_raw is not None
                    else prev.get("ogloszenia", [])
                ),
                "tematy": (
                    _build_tematy(tematy_raw)
                    if tematy_raw is not None
                    else prev.get("tematy", [])
                ),
                "srednie_librus": (
                    _build_srednie_librus(grades_raw["srednie_librus"])
                    if grades_raw
                    else prev.get("srednie_librus", {})
                ),
            }

            if grades is None:
                # Zachowaj poprzednie dane o ocenach jesli dostepne, wiadomosci zaktualizuj jesli OK
                if not prev.get("oceny"):
                    raise UpdateFailed("Nie udalo sie pobrac ocen i brak danych w cache")
                _LOGGER.warning("Nie udalo sie pobrac ocen - uzywam poprzednich danych z cache")
                result = {
                    "student_info": student_info or prev.get("student_info"),
                    "oceny": prev.get("oceny", []),
                    "oceny_wg_przedmiotu": prev.get("oceny_wg_przedmiotu", {}),
                    "wiadomosci": (
                        self._build_wiadomosci(messages)
                        if messages is not None
                        else prev.get("wiadomosci", [])
                    ),
                    "zadania": (
                        self._build_zadania(homework_raw)
                        if homework_raw is not None
                        else prev.get("zadania", [])
                    ),
                    "terminarz": (
                        schedule_raw
                        if schedule_raw is not None
                        else prev.get("terminarz", [])
                    ),
                    "semestr_biezacy": current_sem,
                    **dodatkowe,
                }
                await self._dodaj_szczegoly_zadan(result)
                return result

            # Grupuj oceny wg przedmiotu i oznacz nowe
            oceny_wg_przedmiotu = self._grupuj_oceny(grades)

            wiadomosci = self._build_wiadomosci(messages)
            zadania = self._build_zadania(homework_raw)
            terminarz = schedule_raw if schedule_raw is not None else []

            result = {
                "student_info": student_info,
                "oceny": grades,
                "oceny_wg_przedmiotu": oceny_wg_przedmiotu,
                "wiadomosci": wiadomosci,
                "zadania": zadania,
                "terminarz": terminarz,
                "semestr_biezacy": current_sem,
                **dodatkowe,
            }
            await self._dodaj_szczegoly_zadan(result)
            obecnosc = dodatkowe["obecnosc"]
            ogloszenia = dodatkowe["ogloszenia"]

            # Pierwsze pobranie - tylko zapamietaj stan, nie wysylaj powiadomien
            if self._first_run:
                self._first_run = False
                for msg in wiadomosci:
                    self._seen_message_hrefs.add(_wiadomosc_id(msg))
                for grade in grades:
                    self._seen_grade_ids.add(_ocena_id(grade))
                for zadanie in zadania:
                    self._seen_homework_ids.add(_zadanie_id(zadanie))
                for zdarzenie in terminarz:
                    self._seen_schedule_ids.add(_zdarzenie_id(zdarzenie))
                for wpis in obecnosc:
                    self._seen_attendance_ids.add(self._attendance_id(wpis))
                for o in ogloszenia:
                    self._seen_announcement_ids.add((o["tytul"], o["data"], o["autor"]))
            else:
                await self._dociagnij_tresci(wiadomosci)
                self._fire_events(wiadomosci, grades)
                self._fire_homework_events(zadania)
                self._fire_schedule_events(terminarz)
                self._fire_attendance_events(obecnosc)
                self._fire_ogloszenia_events(ogloszenia)

            return result

        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Blad komunikacji z API: {err}") from err

    async def _dodaj_szczegoly_zadan(self, result: Dict[str, Any]) -> None:
        """Dociagnij tresc zadan z terminem <= 7 dni (z cache; nowe zadania po kilka naraz)."""
        try:
            zadania = result.get("zadania", [])
            aktualne = {z["href"] for z in zadania if z.get("href")}
            self._homework_details = {
                h: d for h, d in self._homework_details.items() if h in aktualne
            }
            if POBIERAJ_TRESC_ZADAN:
                granica = _dzis() + timedelta(days=7)
                brakujace = []
                for z in zadania:
                    href = z.get("href")
                    termin = _parse_date(z.get("termin"))
                    if (
                        href
                        and href not in self._homework_details
                        and termin
                        and termin <= granica
                    ):
                        brakujace.append(href)
                brakujace = brakujace[:MAX_SZCZEGOLOW_NA_ODSWIEZENIE]
                if brakujace:
                    pobrane = await self.client.async_get_homework_details(brakujace)
                    if pobrane is not None:
                        # brakujace (blad pojedynczego zadania) cache'ujemy jako puste,
                        # zeby nie ponawiac zapytania co odswiezenie
                        for href in brakujace:
                            self._homework_details[href] = pobrane.get(href, {})
            result["zadania_szczegoly"] = dict(self._homework_details)
        except Exception as err:  # szczegoly sa opcjonalne - nie psuj calego odswiezenia
            _LOGGER.warning("Nie udalo sie pobrac tresci zadan domowych: %s", err)
            result["zadania_szczegoly"] = dict(self._homework_details)

    def _fire_events(self, messages: List[Dict], grades: List[Dict]) -> None:
        """Wyslij zdarzenia HA dla nowych wiadomosci i ocen."""
        for indeks, msg in enumerate(messages):
            msg_id = _wiadomosc_id(msg)
            if msg_id not in self._seen_message_hrefs:
                self._seen_message_hrefs.add(msg_id)
                _LOGGER.debug("Nowa wiadomosc: %s", msg.get("title"))
                self.hass.bus.fire(
                    EVENT_NOWA_WIADOMOSC,
                    {
                        "nadawca": msg.get("author", ""),
                        "temat": msg.get("title", ""),
                        "data": msg.get("date", ""),
                        "ma_zalacznik": msg.get("has_attachment", False),
                        "nieprzeczytana": msg.get("unread", False),
                        "tresc": msg.get("tresc", ""),
                        "indeks": indeks,
                    },
                )

        for grade in grades:
            grade_id = _ocena_id(grade)
            if grade_id not in self._seen_grade_ids:
                self._seen_grade_ids.add(grade_id)
                _LOGGER.debug("Nowa ocena: %s %s", grade["subject"], grade["grade"])
                self.hass.bus.fire(
                    EVENT_NOWA_OCENA,
                    {
                        "przedmiot": grade["subject"],
                        "ocena": grade["grade"],
                        "data": grade["date"],
                        "kategoria": grade["category"],
                        "nauczyciel": grade["teacher"],
                        "waga": grade.get("weight"),
                        "liczy_sie": grade.get("counts", True),
                        "komentarz": _komentarz_oceny(grade.get("desc", "")),
                    },
                )

    def _fire_schedule_events(self, terminarz: List[Dict]) -> None:
        """Wyslij zdarzenia HA dla nowych zdarzen w kalendarzu."""
        for zdarzenie in terminarz:
            ev_id = _zdarzenie_id(zdarzenie)
            if ev_id not in self._seen_schedule_ids:
                self._seen_schedule_ids.add(ev_id)
                _LOGGER.debug("Nowe zdarzenie: %s %s %s", zdarzenie["data"], zdarzenie["przedmiot"], zdarzenie["tytul"])
                self.hass.bus.fire(
                    EVENT_NOWE_ZDARZENIE,
                    {
                        "data": zdarzenie["data"],
                        "tytul": zdarzenie["tytul"],
                        "przedmiot": zdarzenie["przedmiot"],
                        "godzina": zdarzenie["godzina"],
                    },
                )

    def _fire_ogloszenia_events(self, ogloszenia: List[Dict]) -> None:
        """Wyslij zdarzenia HA dla nowych ogloszen."""
        for o in ogloszenia:
            og_id = (o["tytul"], o["data"], o["autor"])
            if og_id in self._seen_announcement_ids:
                continue
            self._seen_announcement_ids.add(og_id)
            _LOGGER.debug("Nowe ogloszenie: %s", o["tytul"])
            self.hass.bus.fire(
                EVENT_NOWE_OGLOSZENIE,
                {
                    "tytul": o["tytul"],
                    "autor": o["autor"],
                    "data": o["data"],
                    "tresc": o["tresc"][:500],
                },
            )

    @staticmethod
    def _attendance_id(wpis: Dict[str, Any]) -> tuple:
        return (wpis["data"], wpis["godzina_lekcyjna"], wpis["symbol"], wpis["przedmiot"])

    def _fire_attendance_events(self, obecnosc: List[Dict]) -> None:
        """Wyslij zdarzenia HA dla nowych wpisow frekwencji (nb, sp, u, zw)."""
        for wpis in obecnosc:
            att_id = self._attendance_id(wpis)
            if att_id in self._seen_attendance_ids:
                continue
            self._seen_attendance_ids.add(att_id)
            if wpis["symbol"] not in SYMBOLE_ALERTU:
                continue
            _LOGGER.debug("Nowy wpis frekwencji: %s %s %s", wpis["data"], wpis["symbol"], wpis["przedmiot"])
            self.hass.bus.fire(
                EVENT_NOWA_NIEOBECNOSC,
                {
                    "data": wpis["data"],
                    "symbol": wpis["symbol"],
                    "typ": wpis["typ"],
                    "przedmiot": wpis["przedmiot"],
                    "godzina_lekcyjna": wpis["godzina_lekcyjna"],
                    "nauczyciel": wpis["nauczyciel"],
                },
            )

    def _build_wiadomosci(self, messages: Optional[List[Dict]]) -> List[Dict]:
        """Oznacz nowe wiadomosci i zwroc liste."""
        result = []
        for msg in messages or []:
            msg["jest_nowa"] = _jest_nowa(msg.get("date", ""))
            msg["tresc"] = self._tresci.get(_tresc_klucz(msg), "")
            result.append(msg)
        return result

    def _build_zadania(self, homework_raw) -> List[Dict]:
        """Przetworz liste Homework na liste dict, posortowana po terminie."""
        if not homework_raw:
            return []
        zadania = [
            {
                "przedmiot": hw.subject,
                "kategoria": hw.category,
                "nauczyciel": hw.teacher,
                "lekcja": hw.lesson,
                "data_zadania": hw.task_date,
                "termin": hw.completion_date,
                "href": hw.href,
            }
            for hw in homework_raw
        ]
        return sorted(zadania, key=lambda z: z["termin"])

    def _fire_homework_events(self, zadania: List[Dict]) -> None:
        """Wyslij zdarzenia HA dla nowych zadan/sprawdzianow."""
        for zadanie in zadania:
            hw_id = _zadanie_id(zadanie)
            if hw_id not in self._seen_homework_ids:
                self._seen_homework_ids.add(hw_id)
                _LOGGER.debug("Nowe zadanie: %s %s", zadanie["przedmiot"], zadanie["kategoria"])
                self.hass.bus.fire(
                    EVENT_NOWE_ZADANIE,
                    {
                        "przedmiot": zadanie["przedmiot"],
                        "kategoria": zadanie["kategoria"],
                        "termin": zadanie["termin"],
                        "nauczyciel": zadanie["nauczyciel"],
                    },
                )


def _device_info(coordinator: DataUpdateCoordinator, config_entry: ConfigEntry) -> Dict[str, Any]:
    """Zwroc informacje o urzadzeniu."""
    data = coordinator.data or {}
    student_info = data.get("student_info")
    name = student_info.name if student_info else "Librus"
    return {
        "identifiers": {(DOMAIN, config_entry.entry_id)},
        "name": f"Librus - {name}",
        "manufacturer": "Librus",
        "model": "Synergia",
    }


class LibrusEntityMixin:
    """Wspolna logika encji Librus: urzadzenie, dane koordynatora, odswiezanie wg zegara.

    Koordynator odswieza dane co SCAN_INTERVAL, ale "dzis", "teraz" i "jutro" zmieniaja
    sie z uplywem czasu - dlatego encje zalezne od zegara przeliczaja stan z cache'u:
    o polnocy (_odswiez_o_polnocy) i/lub co minute (_odswiez_co_minute).
    """

    _odswiez_o_polnocy = False
    _odswiez_co_minute = False

    def _init_librus(
        self, config_entry: ConfigEntry, name: str, unique_suffix: str, icon: str
    ) -> None:
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = name
        self._attr_unique_id = f"{config_entry.entry_id}_{unique_suffix}"
        self._attr_icon = icon

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def _data(self) -> Dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def _plan(self) -> Dict[str, List[Dict[str, Any]]]:
        return self._data.get("plan") or {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._odswiez_o_polnocy:
            self.async_on_remove(
                async_track_time_change(
                    self.hass, self._odswiez_stan, hour=0, minute=0, second=10
                )
            )
        if self._odswiez_co_minute:
            self.async_on_remove(
                async_track_time_change(self.hass, self._odswiez_stan, second=2)
            )

    @callback
    def _odswiez_stan(self, _now: datetime) -> None:
        self.async_write_ha_state()
