"""The Librus APIX integration."""

import asyncio
import logging
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
from .coordinator import LibrusDataUpdateCoordinator, UWZGLEDNIJ_OCENY_OPISOWE, _biezacy_semestr

_LOGGER = logging.getLogger(__name__)


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
        """Get grades from Librus: {"oceny": [...], "srednie_librus": {...}} albo None."""
        for attempt in range(2):
            try:
                if not self._client or not self._token:
                    if not await self.async_authenticate():
                        return None
                client = self._client

                from librus_apix.grades import get_grades

                loop = asyncio.get_running_loop()
                numeric_grades, average_grades, descriptive_grades = await loop.run_in_executor(
                    None, get_grades, client, "all"
                )

                current_sem = _current_semester()
                _LOGGER.debug("Filtrowanie ocen dla semestru %d", current_sem)

                # Process all grades
                all_grades = []

                # Process numeric grades (only current semester)
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

                # Process descriptive grades (only current semester, many are actually numeric)
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


def _zarejestruj_uslugi(hass: HomeAssistant) -> None:
    """Usluga librus_apix.pobierz_tresc: pobiera i pokazuje tresc klikniętej wiadomosci."""
    if hass.services.has_service(DOMAIN, "przegladaj"):
        return

    async def _pobierz_tresc(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is None:
            _LOGGER.warning("pobierz_tresc: brak aktywnej integracji Librus")
            return
        await coordinator.async_pobierz_tresc(int(call.data["indeks"]))

    async def _przegladaj(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is not None:
            await coordinator.async_przegladaj(call.data["kierunek"])

    hass.services.async_register(
        DOMAIN,
        "przegladaj",
        _przegladaj,
        schema=vol.Schema(
            {
                vol.Required("kierunek"): vol.In(["nastepna", "poprzednia", "najnowsze"]),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )

    async def _przegladaj_ogloszenia(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is not None:
            coordinator.async_przegladaj_ogloszenia(call.data["kierunek"])

    hass.services.async_register(
        DOMAIN,
        "przegladaj_ogloszenia",
        _przegladaj_ogloszenia,
        schema=vol.Schema(
            {
                vol.Required("kierunek"): vol.In(["nastepna", "poprzednia", "najnowsze"]),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )

    async def _przegladaj_terminarz(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is not None:
            coordinator.async_przegladaj_terminarz(call.data["kierunek"])

    hass.services.async_register(
        DOMAIN,
        "przegladaj_terminarz",
        _przegladaj_terminarz,
        schema=vol.Schema(
            {
                vol.Required("kierunek"): vol.In(["nastepna", "poprzednia", "najnowsze"]),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )

    async def _przegladaj_plan(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is not None:
            await coordinator.async_przegladaj_plan(call.data["kierunek"])

    hass.services.async_register(
        DOMAIN,
        "przegladaj_plan",
        _przegladaj_plan,
        schema=vol.Schema(
            {
                vol.Required("kierunek"): vol.In(["nastepny", "poprzedni", "biezacy"]),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )

    async def _przegladaj_zadania(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is not None:
            coordinator.async_przegladaj_zadania(call.data["kierunek"])

    hass.services.async_register(
        DOMAIN,
        "przegladaj_zadania",
        _przegladaj_zadania,
        schema=vol.Schema(
            {
                vol.Required("kierunek"): vol.In(["nastepna", "poprzednia", "najnowsze"]),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )

    async def _przegladaj_oceny(call) -> None:
        coordinators = hass.data.get(DOMAIN, {}).get("coordinators", {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            coordinators.get(entry_id) if entry_id else next(iter(coordinators.values()), None)
        )
        if coordinator is not None:
            coordinator.async_przegladaj_oceny(call.data["kierunek"])

    hass.services.async_register(
        DOMAIN,
        "przegladaj_oceny",
        _przegladaj_oceny,
        schema=vol.Schema(
            {
                vol.Required("kierunek"): vol.In(["nastepna", "poprzednia", "najnowsze"]),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "pobierz_tresc",
        _pobierz_tresc,
        schema=vol.Schema(
            {
                vol.Required("indeks"): vol.All(vol.Coerce(int), vol.Range(min=0, max=50)),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )


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
    
    return unload_ok