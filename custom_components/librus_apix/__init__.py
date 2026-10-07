"""The Librus APIX integration."""

import asyncio
import inspect
import logging
import re
import traceback
from datetime import date
from typing import Dict, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv

from librus_apix.client import Client, new_client
from librus_apix.exceptions import TokenError

from homeassistant.util import dt as dt_util

from .const import DOMAIN, SCAN_INTERVAL
from .coordinator import (
    LibrusDataUpdateCoordinator,
    UWZGLEDNIJ_OCENY_OPISOWE,
    _biezacy_semestr,
    _parse_date,
    _wartosc_oceny,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wlasny, tolerancyjny parser strony z ocenami (uzupelnia biblioteke librus-apix)
# ---------------------------------------------------------------------------

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _pola_z_tytulu(title: str) -> tuple:
    """Rozbij opis oceny ("Klucz: wartosc<br />...") na slownik pol i liste linii (linie bez klucza dolaczamy do poprzedniej)."""
    linie, pola, ostatni = [], {}, None
    for surowa in _BR.split(title or ""):
        linia = " ".join(surowa.split())
        if not linia:
            continue
        klucz, dwukropek, wartosc = linia.partition(":")
        if dwukropek and 0 < len(klucz) <= 40:
            ostatni = klucz.strip()
            pola[ostatni] = wartosc.strip()
            linie.append(f"{ostatni}: {pola[ostatni]}")
        elif ostatni is not None:  # ciag dalszy poprzedniego pola (np. wieloliniowy opis)
            pola[ostatni] = f"{pola[ostatni]} {linia}".strip()
            linie[-1] = f"{ostatni}: {pola[ostatni]}"
        else:
            linie.append(linia)
    return pola, linie


def _oceny_z_html(html: str, semestr: int) -> list:
    """Wszystkie oceny ze strony "Oceny" (kazdy element grade-box z opisem), tylko z podanego semestru.

    Semestr ustalamy z daty oceny (wrzesien-styczen = 1, luty-czerwiec = 2), a nie z kolumny tabeli,
    bo uklad kolumn rozni sie miedzy klasami (np. 1-3 maja inne kolumny niz starsze).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    wynik, widziane = [], set()
    for a in soup.select("span.grade-box a, a.ocena"):
        title = a.attrs.get("title", "")
        if "Data:" not in title:
            continue
        ocena = " ".join(a.get_text().replace("\xa0", " ").split())
        if not ocena:
            continue
        pola, linie = _pola_z_tytulu(title)
        data = (pola.get("Data", "").split(" ")[0] or "")[:10]
        d = _parse_date(data)
        if d is not None and _biezacy_semestr(d) != semestr:
            continue
        wiersz = next(
            (tr for tr in a.find_parents("tr") if {"line0", "line1"} & set(tr.get("class", [])) and not tr.get("id")),
            a.find_parent("tr"),
        )
        przedmiot = ""
        if wiersz is not None:
            komorka = next((td for td in wiersz.find_all("td") if "micro" not in td.get("class", [])), None)
            przedmiot = " ".join(komorka.get_text().split()) if komorka is not None else ""
        przedmiot = przedmiot or "Inne"
        href = a.attrs.get("href", "")
        if "javascript" in href:
            href = ""
        waga = pola.get("Waga", "")
        klucz = (href, przedmiot, ocena, data, pola.get("Kategoria", ""))
        if klucz in widziane:
            continue
        widziane.add(klucz)
        liczy = "Licz do średniej" in pola
        wynik.append({
            "subject": przedmiot,
            "grade": ocena,
            "date": data,
            "category": pola.get("Kategoria") or pola.get("Obszar", ""),
            "teacher": pola.get("Nauczyciel", ""),
            "semester": semestr,
            "type": "descriptive" if _wartosc_oceny(ocena) is None else "numeric",
            "weight": int(waga) if waga.isdigit() else None,
            "counts": pola.get("Licz do średniej", "").strip().lower() == "tak" if liczy else True,
            "href": href,
            "desc": f"Ocena: {ocena}\nPrzedmiot: {przedmiot}\n" + "\n".join(linie),
        })
    return wynik


def _tylko_nowe_oceny(znane: list, kandydaci: list) -> list:
    """Oceny z kandydatow, ktorych nie ma jeszcze wsrod znanych (wg linku albo przedmiot+ocena+data)."""
    hrefy = {g["href"] for g in znane if g.get("href")}
    trojki = {(g["subject"], g["grade"], g["date"]) for g in znane}
    wynik = []
    for g in kandydaci:
        if (g.get("href") and g["href"] in hrefy) or (g["subject"], g["grade"], g["date"]) in trojki:
            continue
        wynik.append(g)
    return wynik


def _to_strona_portalu(html: str) -> bool:
    """Czy to publiczna strona Portalu Librus (niezalogowany) zamiast strony Synergii."""
    h = html or ""
    return "Portal LIBRUS Rodzina" in h or ("niezalogowany" in h and "portal.librus.pl" in h)


def _pobierz_strone_ocen(client) -> tuple:
    """Pobiera strone ocen: POST (jak biblioteka), a gdy to portal - GET. Zwraca (html, opis)."""
    opis = []
    html = ""
    for metoda in ("post", "get"):
        try:
            if metoda == "post":
                resp = client.post(client.GRADES_URL, data={"zmiany_logowanie_wszystkie": "1"})
            else:
                resp = client.get(client.GRADES_URL)
            html = resp.text
            opis.append(f"{metoda.upper()} {client.GRADES_URL} -> {resp.status_code} {resp.url} "
                        f"(przekierowan: {len(resp.history)}, portal: {_to_strona_portalu(html)})")
            if not _to_strona_portalu(html):
                break
        except Exception as ex:  # noqa: BLE001
            opis.append(f"{metoda.upper()} blad: {ex}")
    return html, " | ".join(opis)


def _diagnostyka_ocen(html: str, wynik: Any, semestr: int, info: str = "") -> str:
    """Czytelny raport do wklejenia w razie problemow z ocenami (bez hasel i tokenow)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    wiersze = soup.find_all("tr", attrs={"class": ["line0", "line1"], "id": None})
    pudelka = soup.select("span.grade-box")
    linie = [
        f"Semestr biezacy: {semestr}",
        f"Dlugosc strony ocen: {len(html or '')} znakow",
        f"Wierszy tabeli (line0/line1 bez id): {len(wiersze)}",
        f"Elementow grade-box: {len(pudelka)}",
        f"Pobranie: {info}",
        f"Strona to portal (niezalogowany): {_to_strona_portalu(html)}",
        f"Ocen w wyniku integracji: {len((wynik or {}).get('oceny', []))}",
    ]
    for g in (wynik or {}).get("oceny", [])[:30]:
        linie.append(f"  - {g['subject']} | {g['grade']} | {g['date']} | {g['type']} | {g.get('category', '')}")
    linie.append("")
    linie.append("Pierwsze elementy grade-box (surowy HTML):")
    for el in pudelka[:6]:
        linie.append(str(el)[:1500])
        wiersz = next((tr for tr in el.find_parents("tr")), None)
        if wiersz is not None:
            linie.append("  klasy wiersza: %s, liczba komorek: %d" % (wiersz.get("class"), len(wiersz.find_all("td"))))
    linie.append("")
    linie.append("Wiersze tabeli (tekst komorek, bez pustych):")
    for i, tr in enumerate(wiersze[:40]):
        komorki = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all("td")]
        linie.append(f"  [{i}] " + " | ".join(k[:60] for k in komorki if k))
    linie.append("")
    linie.append("Komorki z tytulem/tooltipem (title) - mozliwe oceny opisowe:")
    n = 0
    for el in soup.select("[title]"):
        t = (el.get("title") or "").strip()
        if t and ("Ocena" in t or "Kategoria" in t or "Data" in t):
            linie.append("  " + el.get_text(" ", strip=True)[:30] + " :: " + t.replace("<br>", " / ").replace("<br/>", " / ")[:300])
            n += 1
            if n >= 15:
                break
    linie.append("")
    linie.append("Surowy HTML sekcji 'Biezace oceny opisowe' (pierwsze 5000 znakow):")
    for h in soup.find_all(["h2", "h3"]):
        if "opisowe" in h.get_text().lower():
            tabela = h.find_next("table")
            linie.append(str(tabela)[:5000] if tabela is not None else "(brak tabeli po naglowku)")
            break
    else:
        linie.append("(nie znaleziono naglowka)")
    linie.append("")
    linie.append("Surowy HTML pierwszego wiersza przedmiotu opisowego (wraz z sasiednimi, takze z id):")
    for tr in soup.select("tr.studentRow"):
        if tr is not None:
            linie.append(str(tr)[:3000])
            for sib in tr.find_next_siblings("tr", limit=2):
                linie.append(str(sib)[:3000])
            break
    linie.append("")
    linie.append("Skrypty strony powiazane z ocenami opisowymi (showHideOO / gradesCell / ajax):")
    n_sk = 0
    for sc in soup.find_all("script"):
        tekst = sc.string or sc.get_text() or ""
        if any(k in tekst for k in ("showHideOO", "gradesCell", "studentGradesDetails", "oceny_opisowe", "OO")):
            linie.append("--- <script> (" + str(len(tekst)) + " znakow) ---")
            for k in ("showHideOO", "gradesCell", "studentGradesDetails", "ajax", "url"):
                for m in re.finditer(re.escape(k), tekst):
                    linie.append("  ..." + " ".join(tekst[max(0, m.start() - 150):m.end() + 350].split()))
                    break
            n_sk += 1
            if n_sk >= 6:
                break
    if not n_sk:
        linie.append("(brak)")
    linie.append("")
    linie.append("Naglowki i linki zwiazane z ocenami:")
    for h in soup.select("h1, h2, h3, thead th")[:25]:
        linie.append("  H: " + " ".join(h.get_text(" ", strip=True).split())[:100])
    widziane = set()
    for a in soup.find_all("a", href=True):
        if "ocen" in a["href"].lower() and a["href"] not in widziane:
            widziane.add(a["href"])
            linie.append("  A: " + a["href"] + " :: " + a.get_text(" ", strip=True)[:50])
            if len(widziane) >= 25:
                break
    return "\n".join(linie)


def _current_semester() -> int:
    """Zwroc numer biezacego semestru (1 lub 2) wg polskiego roku szkolnego.

    Semestr 1: wrzesien (9) - styczen (1)
    Semestr 2: luty (2) - czerwiec (6)
    Lipiec-sierpien to wakacje - zwracamy 2 (ostatni semestr roku).
    """
    return _biezacy_semestr(dt_util.now().date())

def _termin_od(termin: str, dzis) -> bool:
    """True, gdy termin ("2026-10-09 piatek") to dzis lub pozniej; nieczytelny termin zostaje."""
    from datetime import datetime as _dt
    try:
        return _dt.strptime(str(termin).strip()[:10], "%Y-%m-%d").date() >= dzis
    except ValueError:
        return True


PLATFORMS = ["sensor", "binary_sensor", "calendar"]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


class LibrusApiClient:
    """Class to interface with the Librus API."""

    def __init__(self, username: str, password: str):
        """Initialize the client."""
        self.username = username
        self.password = password
        self._client: Client = None
        self._token = None
        self._auth_lock = asyncio.Lock()
        self._ostatnia_strona_ocen = ""

    def _reset_auth(self) -> None:
        """Reset authentication state to force re-authentication on next call."""
        self._client = None
        self._token = None

    async def async_authenticate(self):
        """Authenticate with Librus API."""
        async with self._auth_lock:
            try:
                loop = asyncio.get_running_loop()
                self._client = await loop.run_in_executor(None, new_client)
                self._token = await loop.run_in_executor(
                    None, self._client.get_token, self.username, self.password
                )
                _LOGGER.debug("Authentication successful for %s", self.username)
                return True
            except Exception as ex:
                _LOGGER.error("Authentication failed: %s\n%s", ex, traceback.format_exc())
                self._reset_auth()
                return False

    async def async_get_grades(self):
        """Get grades from Librus: {"oceny": [...], "srednie_librus": {...}} albo None.

        Oceny czytamy dwiema drogami: biblioteka librus-apix (oceny liczbowe, opisowe i srednie)
        oraz wlasnym, tolerancyjnym parserem tej samej strony (patrz _oceny_z_html) - dzieki temu
        ocena, ktorej biblioteka nie rozpozna (np. opisowa "Uz" w klasach 1-3) albo ktora wywroci
        jej parser, nie znika z listy. Wyniki sa laczone bez duplikatow.
        """
        current_sem = _current_semester()
        _LOGGER.debug("Filtrowanie ocen dla semestru %d", current_sem)

        def _fetch(client):
            from librus_apix.grades import get_grades
            from librus_apix.helpers import no_access_check
            from bs4 import BeautifulSoup

            wynik_biblioteki, blad = None, None
            try:
                wynik_biblioteki = get_grades(client, "all")
            except TokenError:
                raise
            except Exception as ex:  # parser biblioteki nie radzi sobie z ta strona - zostaje nasz
                blad = ex
                _LOGGER.warning("Biblioteka librus-apix nie odczytala ocen (%s) - uzywam wlasnego parsera", ex)
            html, info = _pobierz_strone_ocen(client)
            self._info_pobrania_ocen = info
            if _to_strona_portalu(html):
                _LOGGER.warning("Strona ocen to publiczny portal Librus (sesja nieaktywna): %s", info)
                raise TokenError("Grades page returned the logged-out portal page")
            no_access_check(BeautifulSoup(html, "lxml"))  # TokenError, gdy sesja wygasla
            return wynik_biblioteki, html, blad

        for attempt in range(2):
            try:
                if not self._client or not self._token:
                    if not await self.async_authenticate():
                        return None
                loop = asyncio.get_running_loop()
                wynik_biblioteki, html, blad = await loop.run_in_executor(None, _fetch, self._client)
                self._ostatnia_strona_ocen = html  # do diagnostyki

                all_grades: list = []
                srednie_librus: dict = {}
                if wynik_biblioteki is not None:
                    numeric_grades, average_grades, descriptive_grades = wynik_biblioteki
                    for subject_grades in numeric_grades:
                        for subject, grades_list in subject_grades.items():
                            for grade in grades_list:
                                if grade.semester != current_sem:
                                    continue
                                all_grades.append({
                                    'subject': subject,
                                    'grade': grade.grade,
                                    'date': grade.date,
                                    'category': grade.category,
                                    'teacher': getattr(grade, 'teacher', ''),
                                    'semester': grade.semester,
                                    'type': 'numeric',
                                    'weight': grade.weight,
                                    'counts': grade.counts,
                                    'href': grade.href,
                                    'desc': grade.desc,
                                })
                    for subject_grades in descriptive_grades:
                        for subject, grades_list in subject_grades.items():
                            for desc_grade in grades_list:
                                if desc_grade.semester != current_sem:
                                    continue
                                grade_val = desc_grade.grade.strip()
                                if grade_val and (UWZGLEDNIJ_OCENY_OPISOWE or
                                                grade_val.replace('+', '').replace('-', '').isdigit() or
                                                grade_val in ['1', '2', '3', '4', '5', '6', '1+', '1-', '2+', '2-',
                                                             '3+', '3-', '4+', '4-', '5+', '5-', '6+', '6-']):
                                    all_grades.append({
                                        'subject': subject,
                                        'grade': desc_grade.grade,
                                        'date': desc_grade.date,
                                        'category': getattr(desc_grade, 'desc', '').split('\n')[0] if hasattr(desc_grade, 'desc') else '',
                                        'teacher': getattr(desc_grade, 'teacher', ''),
                                        'semester': desc_grade.semester,
                                        'type': 'descriptive',
                                        'href': getattr(desc_grade, 'href', ''),
                                        'desc': getattr(desc_grade, 'desc', ''),
                                    })
                    # Oficjalne srednie Librusa: {przedmiot: {semestr: gpa}}, semestr 0 = roczna
                    srednie_librus = {
                        subject: {g.semester: g.gpa for g in gpa_list}
                        for subject, gpa_list in average_grades.items()
                    }

                # Wlasny parser: dopisz to, czego biblioteka nie zwrocila
                dodatkowe = _tylko_nowe_oceny(all_grades, _oceny_z_html(html, current_sem))
                if dodatkowe:
                    _LOGGER.info("Wlasny parser dopisal %d ocen pominietych przez biblioteke", len(dodatkowe))
                    all_grades = all_grades + dodatkowe
                if wynik_biblioteki is None and not all_grades and blad is not None:
                    raise blad

                return {"oceny": all_grades, "srednie_librus": srednie_librus}

            except TokenError as ex:
                _LOGGER.warning(
                    "Token expired fetching grades (attempt %d/2), re-authenticating...",
                    attempt + 1,
                )
                self._reset_auth()
                if attempt == 1:
                    _LOGGER.error("Failed to get grades after re-authentication.")
                    return None
            except Exception as ex:
                _LOGGER.error(
                    "Failed to get grades (attempt %d/2): %s\n%s",
                    attempt + 1, ex, traceback.format_exc(),
                )
                self._reset_auth()
                if attempt == 1:
                    return None

    async def async_diagnostyka_ocen(self) -> str:
        """Tekst diagnostyczny: co widzi biblioteka, a co wlasny parser na stronie ocen."""
        wynik = await self.async_get_grades()
        html = getattr(self, "_ostatnia_strona_ocen", "") or ""
        return _diagnostyka_ocen(html, wynik, _current_semester(), getattr(self, "_info_pobrania_ocen", ""))

    async def async_get_messages(self, count: int = 10, page: int = 0):
        """Pobierz najnowsze wiadomosci (nadawca, temat, data) - bez tresci, zeby nie oznaczac ich jako przeczytane.

        Przy bledzie innym niz TokenError zwraca None bez resetu sesji (funkcja jest wolana
        co kilka minut, wiec nie moze wymuszac ponownego logowania ani zasmiecac logu bledami).
        """
        from librus_apix.messages import get_received

        def _fetch(client):
            return (get_received(client, page) or [])[:count]

        messages = await self._async_call("messages", _fetch)
        if messages is None:
            return None

        def _czysty(text: Any) -> str:
            return " ".join(str(text or "").split())

        return [
            {
                "author": _czysty(msg.author),
                "title": _czysty(msg.title),
                "date": _czysty(msg.date),
                "href": msg.href,
                "unread": msg.unread,
                "has_attachment": msg.has_attachment,
            }
            for msg in messages
        ]

    async def async_get_message_content(self, href: str):
        """Pobierz tresc wiadomosci. UWAGA: otwiera wiadomosc w Librusie (oznacza jako przeczytana)."""
        from librus_apix.messages import message_content

        data = await self._async_call(
            "message content", lambda client: message_content(client, href)
        )
        return data.content if data else None

    async def async_get_homework(self):
        """Pobierz zadania domowe z terminem od dzis wzwyz.

        Filtr dat w Librusie (dataOd/dataDo) najpewniej dotyczy DATY ZADANIA, a nie terminu
        (biblioteka w swoich powiadomieniach pyta o zakres [dzis-7, dzis]). Samo okno
        [dzis, dzis+30] pomijalo wiec zadania zadane wczoraj z terminem za tydzien.
        Pytamy o oba okna - zadane w ostatnich 30 dniach i na najblizsze 30 dni - i laczymy.
        """
        from librus_apix.homework import get_homework
        from datetime import timedelta

        def _fetch(client):
            today = dt_util.now().date()
            okna = (
                (today - timedelta(days=30), today),
                (today, today + timedelta(days=30)),
            )
            wynik, widziane, pobrano, blad = [], set(), 0, None
            for od, do in okna:
                try:
                    lista = get_homework(client, od.isoformat(), do.isoformat())
                except TokenError:
                    raise
                except Exception as ex:
                    blad = ex
                    _LOGGER.debug("Homework window %s..%s unavailable: %s", od, do, ex)
                    continue
                pobrano += 1
                for hw in lista:
                    klucz = hw.href or (hw.subject, hw.lesson, hw.task_date, hw.completion_date)
                    if klucz in widziane:
                        continue
                    widziane.add(klucz)
                    wynik.append(hw)
            if not pobrano:
                raise blad  # oba zapytania nieudane
            # tylko zadania z terminem dzis lub pozniej (jak dotad)
            return [hw for hw in wynik if _termin_od(hw.completion_date, today)]

        return await self._async_call("homework", _fetch)

    async def async_get_schedule(self):
        """Get upcoming calendar events from Librus (current + next month, filtered to future dates)."""
        for attempt in range(2):
            try:
                if not self._client or not self._token:
                    if not await self.async_authenticate():
                        return None

                from librus_apix.schedule import get_schedule
                from datetime import date as _date
                import calendar

                today = dt_util.now().date()
                loop = asyncio.get_running_loop()

                def _fetch_two_months():
                    events = []
                    for year, month in [
                        (today.year, today.month),
                        (
                            today.year + 1 if today.month == 12 else today.year,
                            1 if today.month == 12 else today.month + 1,
                        ),
                    ]:
                        monthly = get_schedule(self._client, str(month).zfill(2), str(year))
                        for day_num, day_events in monthly.items():
                            event_date = _date(year, month, int(day_num))
                            if event_date < today:
                                continue
                            for ev in day_events:
                                events.append({
                                    "data": event_date.strftime("%Y-%m-%d"),
                                    "tydzien": event_date.strftime("%A"),
                                    "tytul": ev.title,
                                    "przedmiot": ev.subject,
                                    "godzina": ev.hour,
                                    "numer_lekcji": ev.number,
                                    "szczegoly": ev.data,
                                    "href": ev.href,
                                })
                    return sorted(events, key=lambda e: e["data"])

                return await loop.run_in_executor(None, _fetch_two_months)

            except TokenError:
                _LOGGER.warning(
                    "Token expired fetching schedule (attempt %d/2), re-authenticating...",
                    attempt + 1,
                )
                self._reset_auth()
                if attempt == 1:
                    _LOGGER.error("Failed to get schedule after re-authentication.")
                    return None
            except Exception as ex:
                _LOGGER.error(
                    "Failed to get schedule (attempt %d/2): %s\n%s",
                    attempt + 1, ex, traceback.format_exc(),
                )
                self._reset_auth()
                if attempt == 1:
                    return None

    async def _async_call(self, label: str, func):
        """Wykonaj func(client) w watku; przy TokenError zaloguj ponownie i sprobuj jeszcze raz.

        Dla funkcji opcjonalnych (plan lekcji, frekwencja): inne bledy niz TokenError
        zwracaja None BEZ resetu sesji, zeby nie wymuszac ponownego logowania co odswiezenie.
        """
        for attempt in range(2):
            try:
                if not self._client or not self._token:
                    if not await self.async_authenticate():
                        return None
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, func, self._client)
            except TokenError:
                _LOGGER.warning(
                    "Token expired fetching %s (attempt %d/2), re-authenticating...",
                    label, attempt + 1,
                )
                self._reset_auth()
                if attempt == 1:
                    _LOGGER.error("Failed to get %s after re-authentication.", label)
                    return None
            except Exception as ex:
                _LOGGER.warning("Failed to get %s: %s", label, ex)
                _LOGGER.debug("Traceback (%s):\n%s", label, traceback.format_exc())
                return None

    async def async_get_timetable(self, poniedzialki=None):
        """Pobierz plan lekcji (plaska lista Period).

        Domyslnie biezacy i nastepny tydzien; `poniedzialki` (lista dat) - dowolne inne tygodnie.
        """
        from librus_apix.timetable import get_timetable
        from datetime import datetime as _datetime, timedelta

        def _fetch(client):
            if poniedzialki is None:
                today = dt_util.now().date()
                monday = today - timedelta(days=today.weekday())
                tygodnie = [monday, monday + timedelta(days=7)]
            else:
                tygodnie = list(poniedzialki)
            periods = []
            fetched = False
            for monday_date in tygodnie:
                week_monday = _datetime.combine(monday_date, _datetime.min.time())
                try:
                    week = get_timetable(client, week_monday)
                except TokenError:
                    raise
                except Exception as ex:
                    # np. wakacje / tydzien bez planu - nie traktuj jako bledu krytycznego
                    _LOGGER.debug("Timetable for week %s unavailable: %s", monday_date, ex)
                    continue
                fetched = True
                for day in week:
                    periods.extend(day)
            return periods if fetched else None

        return await self._async_call("timetable", _fetch)

    async def async_get_attendance(self):
        """Pobierz wpisy frekwencji (lista dwoch list: semestr 1 i semestr 2)."""
        from librus_apix.attendance import get_attendance

        return await self._async_call(
            "attendance", lambda client: get_attendance(client, "all")
        )

    async def async_get_attendance_frequency(self):
        """Pobierz frekwencje procentowa: krotka (semestr 1, semestr 2, ogolem), wartosci 0-1."""
        from librus_apix.attendance import get_attendance_frequency

        return await self._async_call("attendance frequency", get_attendance_frequency)

    async def async_get_homework_details(self, hrefs):
        """Pobierz szczegoly (tresc) zadan domowych: {href: {etykieta: wartosc}}."""
        if not hrefs:
            return {}
        from librus_apix.homework import homework_detail

        def _fetch(client):
            details = {}
            for href in hrefs:
                try:
                    details[href] = homework_detail(client, href)
                except TokenError:
                    raise
                except Exception as ex:
                    _LOGGER.debug("Homework detail %s unavailable: %s", href, ex)
            return details

        return await self._async_call("homework details", _fetch)

    async def async_get_announcements(self):
        """Pobierz ogloszenia szkoly (lista Announcement)."""
        from librus_apix.announcements import get_announcements

        return await self._async_call("announcements", get_announcements)

    async def async_get_completed_lessons(self):
        """Pobierz zrealizowane lekcje z tematami (ostatnie 4 dni, max 4 strony)."""
        from datetime import timedelta
        from librus_apix.completed_lessons import get_completed, get_max_page_number

        def _fetch(client):
            today = dt_util.now().date()
            date_from = (today - timedelta(days=3)).isoformat()
            date_to = today.isoformat()
            pages = max(1, min(get_max_page_number(client, date_from, date_to), 4))
            lessons = []
            for page in range(pages):
                lessons.extend(get_completed(client, date_from, date_to, page))
            return lessons

        return await self._async_call("completed lessons", _fetch)

    async def async_get_student_information(self):
        """Get student information from Librus."""
        try:
            if not self._client or not self._token:
                if not await self.async_authenticate():
                    return None

            from librus_apix.student_information import get_student_information

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, get_student_information, self._client)

        except Exception as ex:
            _LOGGER.error(
                "Failed to get student information: %s\n%s", ex, traceback.format_exc()
            )
            self._reset_auth()
            return None


async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    """Set up the Librus APIX component."""
    hass.data.setdefault(DOMAIN, {})
    
    if DOMAIN in config:
        username = config[DOMAIN][CONF_USERNAME]
        password = config[DOMAIN][CONF_PASSWORD]
        
        client = LibrusApiClient(username, password)
        hass.data[DOMAIN]["client"] = client
        
        # Test authentication
        if not await client.async_authenticate():
            _LOGGER.error("Failed to authenticate")
            return False

    return True


_KIERUNKI = ["nastepna", "poprzednia", "najnowsze"]
# usluga -> (metoda koordynatora, nazwa glownego pola, dozwolone wartosci albo None = liczba 0-50)
_USLUGI = {
    "pobierz_tresc": ("async_pobierz_tresc", "indeks", None),
    "przegladaj": ("async_przegladaj", "kierunek", _KIERUNKI),
    "przegladaj_ogloszenia": ("async_przegladaj_ogloszenia", "kierunek", _KIERUNKI),
    "przegladaj_terminarz": ("async_przegladaj_terminarz", "kierunek", _KIERUNKI),
    "przegladaj_zadania": ("async_przegladaj_zadania", "kierunek", _KIERUNKI),
    "przegladaj_oceny": ("async_przegladaj_oceny", "kierunek", _KIERUNKI),
    "przegladaj_plan": ("async_przegladaj_plan", "kierunek", ["nastepny", "poprzedni", "biezacy"]),
}


def _zarejestruj_uslugi(hass: HomeAssistant) -> None:
    """Rejestruje uslugi integracji (kazda osobno, tylko jesli jeszcze nie istnieje)."""

    def _fabryka(metoda: str, pole: str):
        async def _obsluga(call) -> None:
            coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
            entry_id = call.data.get("config_entry_id")
            coordinator = (
                coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
            )
            if coordinator is None:
                _LOGGER.warning("%s: brak aktywnej integracji Librus", metoda)
                return
            wartosc = call.data[pole]
            wynik = getattr(coordinator, metoda)(int(wartosc) if pole == "indeks" else wartosc)
            if inspect.isawaitable(wynik):
                await wynik

        return _obsluga

    if not hass.services.has_service(DOMAIN, "diagnostyka_ocen"):

        async def _diagnostyka(call) -> None:
            coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
            entry_id = call.data.get("config_entry_id")
            coordinator = (
                coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
            )
            if coordinator is None:
                return
            raport = await coordinator.client.async_diagnostyka_ocen()
            sciezka = hass.config.path("librus_apix_diagnostyka_ocen.txt")

            strona = getattr(coordinator.client, "_ostatnia_strona_ocen", "") or ""
            sciezka_html = hass.config.path("librus_apix_strona_ocen.html")

            def _zapisz() -> None:
                with open(sciezka, "w", encoding="utf-8") as plik:
                    plik.write(raport)
                with open(sciezka_html, "w", encoding="utf-8") as plik:
                    plik.write(strona)

            await hass.async_add_executor_job(_zapisz)
            _LOGGER.warning("Raport diagnostyczny ocen zapisano w %s", sciezka)

        hass.services.async_register(
            DOMAIN, "diagnostyka_ocen", _diagnostyka, schema=vol.Schema({vol.Optional("config_entry_id"): str})
        )

    for nazwa, (metoda, pole, dozwolone) in _USLUGI.items():
        if hass.services.has_service(DOMAIN, nazwa):
            continue
        walidator = (
            vol.All(vol.Coerce(int), vol.Range(min=0, max=50)) if dozwolone is None else vol.In(dozwolone)
        )
        hass.services.async_register(
            DOMAIN,
            nazwa,
            _fabryka(metoda, pole),
            schema=vol.Schema({vol.Required(pole): walidator, vol.Optional("config_entry_id"): str}),
        )
    _LOGGER.debug("Zarejestrowane uslugi: %s", ", ".join(_USLUGI))


def _wyrejestruj_uslugi(hass: HomeAssistant) -> None:
    for nazwa in (*_USLUGI, "diagnostyka_ocen"):
        if hass.services.has_service(DOMAIN, nazwa):
            hass.services.async_remove(DOMAIN, nazwa)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Librus APIX from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    
    client = LibrusApiClient(username, password)
    
    # Test authentication
    if not await client.async_authenticate():
        _LOGGER.error("Failed to authenticate")
        return False
    
    coordinator = LibrusDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_wczytaj_tresci()
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_uruchom_odswiezanie(entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = client
    hass.data[DOMAIN].setdefault("coordinators", {})[entry.entry_id] = coordinator
    
    _zarejestruj_uslugi(hass)

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.data[DOMAIN].get("coordinators", {}).pop(entry.entry_id, None)
        if not hass.data[DOMAIN].get("coordinators"):
            _wyrejestruj_uslugi(hass)  # ostatni wpis - zdejmij uslugi (przy ponownym ladowaniu wroca)
    
    return unload_ok