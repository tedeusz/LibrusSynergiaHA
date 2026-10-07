"""Platforma czujników dla integracji Librus APIX."""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _jest_nowa(date_str: str) -> bool:
    """Sprawdz czy data miesci sie w ostatnich 24 godzinach (dzis lub wczoraj)."""
    if not date_str:
        return False
    wczoraj = date.today() - timedelta(days=1)
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


def _srednia_ocen(oceny: List[Dict]) -> Optional[float]:
    """Oblicz srednia ocen z listy ocen."""
    wartosci = []
    for g in oceny:
        grade_str = g.get("ocena", "")
        try:
            base = float(grade_str[0])
            if len(grade_str) > 1:
                if "+" in grade_str:
                    base += 0.5
                elif "-" in grade_str:
                    base -= 0.25
            wartosci.append(base)
        except (ValueError, IndexError):
            continue
    return round(sum(wartosci) / len(wartosci), 2) if wartosci else None


# Pobieraj tresc zadan domowych (osobne zapytanie na kazde zadanie z terminem <= 7 dni).
# Wylacz (False), jesli nie chcesz dodatkowych zapytan do Librusa.
POBIERAJ_TRESC_ZADAN = True
MAX_SZCZEGOLOW_NA_ODSWIEZENIE = 10

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
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Konfiguracja platformy czujnikow Librus APIX."""
    client = hass.data[DOMAIN][config_entry.entry_id]

    coordinator = LibrusDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    entities: List[SensorEntity] = [
        LibrusUczenSensor(coordinator, config_entry),
        LibrusSzczesliwyNumerekSensor(coordinator, config_entry),
        LibrusOcenySensor(coordinator, config_entry),
        LibrusWiadomosciSensor(coordinator, config_entry),
        LibrusZadaniaSensor(coordinator, config_entry),
        LibrusTerminarzSensor(coordinator, config_entry),
        LibrusPlanLekcjiSensor(coordinator, config_entry, "dzis"),
        LibrusPlanLekcjiSensor(coordinator, config_entry, "nastepny"),
        LibrusPlanTygodniaSensor(coordinator, config_entry),
        LibrusZadaniaDomoweSensor(coordinator, config_entry),
        LibrusFrekwencjaSensor(coordinator, config_entry),
        LibrusNieobecnosciSensor(coordinator, config_entry),
    ]

    # Tworz czujniki per przedmiot na podstawie pierwszego pobrania danych
    for subject in coordinator.data.get("oceny_wg_przedmiotu", {}).keys():
        entities.append(LibrusPrzedmiotSensor(coordinator, subject, config_entry))
        entities.append(LibrusSredniaPrzedmiotuSensor(coordinator, subject, config_entry))

    # Czujnik globalnej sredniej
    entities.append(LibrusSredniaOcenSensor(coordinator, config_entry))

    async_add_entities(entities)


EVENT_NOWA_WIADOMOSC = f"{DOMAIN}_nowa_wiadomosc"
EVENT_NOWA_OCENA = f"{DOMAIN}_nowa_ocena"
EVENT_NOWE_ZADANIE = f"{DOMAIN}_nowe_zadanie"
EVENT_NOWE_ZDARZENIE = f"{DOMAIN}_nowe_zdarzenie"
EVENT_NOWA_NIEOBECNOSC = f"{DOMAIN}_nowa_nieobecnosc"


class LibrusDataUpdateCoordinator(DataUpdateCoordinator):
    """Klasa zarzadzajaca pobieraniem danych z Librus."""

    def __init__(self, hass: HomeAssistant, client: Any) -> None:
        """Inicjalizacja koordynatora."""
        self.client = client
        self._seen_message_hrefs: set = set()
        self._seen_grade_ids: set = set()
        self._seen_homework_ids: set = set()
        self._seen_schedule_ids: set = set()
        self._seen_attendance_ids: set = set()
        self._homework_details: Dict[str, Dict[str, str]] = {}
        self._first_run: bool = True
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Pobierz aktualne dane z API Librus."""
        from datetime import date as _date
        current_sem = 1 if _date.today().month >= 9 else 2

        try:
            student_info = await self.client.async_get_student_information()
            grades = await self.client.async_get_grades()
            messages = await self.client.async_get_messages(count=10)
            homework_raw = await self.client.async_get_homework()
            schedule_raw = await self.client.async_get_schedule()
            plan_raw = await self.client.async_get_timetable()
            attendance_raw = await self.client.async_get_attendance()
            frequency_raw = await self.client.async_get_attendance_frequency()

            # Plan lekcji i frekwencja: przy bledzie pobierania zostaja poprzednie dane
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
            oceny_wg_przedmiotu: Dict[str, List[Dict]] = {}
            for grade in grades:
                subject = grade["subject"]
                if subject not in oceny_wg_przedmiotu:
                    oceny_wg_przedmiotu[subject] = []
                oceny_wg_przedmiotu[subject].append({
                    "ocena": grade["grade"],
                    "data": grade["date"],
                    "kategoria": grade["category"],
                    "nauczyciel": grade["teacher"],
                    "semestr": grade.get("semester"),
                    "jest_nowa": _jest_nowa(grade["date"]),
                })

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

            # Pierwsze pobranie - tylko zapamietaj stan, nie wysylaj powiadomien
            if self._first_run:
                self._first_run = False
                for msg in wiadomosci:
                    self._seen_message_hrefs.add(msg["href"])
                for grade in grades:
                    self._seen_grade_ids.add(
                        (grade["subject"], grade["date"], grade["grade"])
                    )
                for zadanie in zadania:
                    self._seen_homework_ids.add(
                        (zadanie["przedmiot"], zadanie["termin"], zadanie["kategoria"])
                    )
                for zdarzenie in terminarz:
                    self._seen_schedule_ids.add(
                        (zdarzenie["data"], zdarzenie["tytul"], zdarzenie["przedmiot"])
                    )
                for wpis in obecnosc:
                    self._seen_attendance_ids.add(self._attendance_id(wpis))
            else:
                self._fire_events(wiadomosci, grades)
                self._fire_homework_events(zadania)
                self._fire_schedule_events(terminarz)
                self._fire_attendance_events(obecnosc)

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
                granica = date.today() + timedelta(days=7)
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
        for msg in messages:
            href = msg.get("href", "")
            if href and href not in self._seen_message_hrefs:
                self._seen_message_hrefs.add(href)
                _LOGGER.debug("Nowa wiadomosc: %s", msg.get("title"))
                self.hass.bus.fire(
                    EVENT_NOWA_WIADOMOSC,
                    {
                        "nadawca": msg.get("author", ""),
                        "temat": msg.get("title", ""),
                        "data": msg.get("date", ""),
                        "ma_zalacznik": msg.get("has_attachment", False),
                    },
                )

        for grade in grades:
            grade_id = (grade["subject"], grade["date"], grade["grade"])
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
                    },
                )

    def _fire_schedule_events(self, terminarz: List[Dict]) -> None:
        """Wyslij zdarzenia HA dla nowych zdarzen w kalendarzu."""
        for zdarzenie in terminarz:
            ev_id = (zdarzenie["data"], zdarzenie["tytul"], zdarzenie["przedmiot"])
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
            hw_id = (zadanie["przedmiot"], zadanie["termin"], zadanie["kategoria"])
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


class LibrusUczenSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z informacjami o uczniu."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Informacje o uczniu"
        self._attr_unique_id = f"{config_entry.entry_id}_uczen"
        self._attr_icon = "mdi:account-school"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[str]:
        info = (self.coordinator.data or {}).get("student_info")
        return info.name if info else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        info = (self.coordinator.data or {}).get("student_info")
        if not info:
            return {}
        return {
            "klasa": info.class_name,
            "numer_w_klasie": info.number,
            "wychowawca": info.tutor,
            "szkola": info.school,
            "szczesliwy_numerek": info.lucky_number,
        }


class LibrusSzczesliwyNumerekSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ze szczesliwym numerkiem dnia."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Szczesliwy numerek"
        self._attr_unique_id = f"{config_entry.entry_id}_szczesliwy_numerek"
        self._attr_icon = "mdi:numeric"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Any:
        info = (self.coordinator.data or {}).get("student_info")
        return info.lucky_number if info else None


class LibrusOcenySensor(CoordinatorEntity, SensorEntity):
    """Czujnik z wszystkimi ocenami pogrupowanymi wedlug przedmiotow."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Oceny"
        self._attr_unique_id = f"{config_entry.entry_id}_oceny"
        self._attr_icon = "mdi:school"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("oceny", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        oceny_wg_przedmiotu = data.get("oceny_wg_przedmiotu", {})
        sa_nowe = any(
            g["jest_nowa"]
            for grades in oceny_wg_przedmiotu.values()
            for g in grades
        )
        return {
            "oceny_wg_przedmiotu": oceny_wg_przedmiotu,
            "liczba_ocen": len((self.coordinator.data or {}).get("oceny", [])),
            "liczba_przedmiotow": len(oceny_wg_przedmiotu),
            "sa_nowe_oceny": sa_nowe,
            "semestr": data.get("semestr_biezacy"),
        }


class LibrusPrzedmiotSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z ocenami dla konkretnego przedmiotu."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        subject: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._subject = subject
        safe_name = subject.lower().replace(" ", "_").replace("/", "_")
        self._attr_has_entity_name = False
        self._attr_name = subject
        self._attr_unique_id = f"{config_entry.entry_id}_przedmiot_{safe_name}"
        self._attr_icon = "mdi:book-open-variant"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[str]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        if not oceny:
            return None
        return ", ".join(g["ocena"] for g in oceny)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        if not oceny:
            return {}

        srednia = _srednia_ocen(oceny)

        # Najnowsza ocena wg daty
        najnowsza: Optional[Dict] = None
        najnowsza_data: Optional[date] = None
        for g in oceny:
            for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                try:
                    d = datetime.strptime(g["data"].strip(), fmt).date()
                    if najnowsza_data is None or d > najnowsza_data:
                        najnowsza_data = d
                        najnowsza = g
                    break
                except ValueError:
                    continue

        return {
            "oceny": oceny,
            "lista_ocen": ", ".join(g["ocena"] for g in oceny),
            "srednia": srednia,
            "najnowsza_ocena": najnowsza,
            "sa_nowe_oceny": any(g["jest_nowa"] for g in oceny),
        }


class LibrusSredniaOcenSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ze srednia wszystkich ocen biezacego semestru (do wykresu)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Srednia ocen"
        self._attr_unique_id = f"{config_entry.entry_id}_srednia_ocen"
        self._attr_icon = "mdi:chart-line"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[float]:
        data = self.coordinator.data or {}
        wszystkie = [
            g
            for oceny in data.get("oceny_wg_przedmiotu", {}).values()
            for g in oceny
        ]
        return _srednia_ocen(wszystkie)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        srednie_przedmiotow = {
            subject: _srednia_ocen(oceny)
            for subject, oceny in data.get("oceny_wg_przedmiotu", {}).items()
            if _srednia_ocen(oceny) is not None
        }
        return {
            "srednie_wg_przedmiotow": srednie_przedmiotow,
            "semestr": data.get("semestr_biezacy"),
        }


class LibrusSredniaPrzedmiotuSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ze srednia ocen dla konkretnego przedmiotu (do wykresu)."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        subject: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._subject = subject
        safe_name = subject.lower().replace(" ", "_").replace("/", "_")
        self._attr_has_entity_name = False
        self._attr_name = f"Srednia {subject}"
        self._attr_unique_id = f"{config_entry.entry_id}_srednia_{safe_name}"
        self._attr_icon = "mdi:chart-bar"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[float]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        return _srednia_ocen(oceny)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        return {
            "przedmiot": self._subject,
            "lista_ocen": ", ".join(g["ocena"] for g in oceny),
            "liczba_ocen": len(oceny),
        }


class LibrusTerminarzSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z nadchodzacymi zdarzeniami z kalendarza Librusa (biezacy + nastepny miesiac)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Terminarz"
        self._attr_unique_id = f"{config_entry.entry_id}_terminarz"
        self._attr_icon = "mdi:calendar-month"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("terminarz", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        terminarz = (self.coordinator.data or {}).get("terminarz", [])
        typy: Dict[str, int] = {}
        for z in terminarz:
            t = z.get("tytul", "")
            typy[t] = typy.get(t, 0) + 1
        return {
            "zdarzenia": terminarz,
            "liczba_zdarzen": len(terminarz),
            "typy": typy,
        }


class LibrusZadaniaSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z nadchodzacymi zadaniami i sprawdzianami (30 dni do przodu)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Zadania"
        self._attr_unique_id = f"{config_entry.entry_id}_zadania"
        self._attr_icon = "mdi:calendar-check"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("zadania", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        zadania = (self.coordinator.data or {}).get("zadania", [])
        kategorie: Dict[str, int] = {}
        for z in zadania:
            k = z.get("kategoria", "")
            kategorie[k] = kategorie.get(k, 0) + 1
        return {
            "zadania": zadania,
            "liczba_zadan": len(zadania),
            "kategorie": kategorie,
        }


class LibrusWiadomosciSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z wiadomosciami (temat i nadawca, bez pobierania tresci)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Wiadomosci"
        self._attr_unique_id = f"{config_entry.entry_id}_wiadomosci"
        self._attr_icon = "mdi:message-text"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        """Liczba nieprzeczytanych wiadomosci."""
        msgs = (self.coordinator.data or {}).get("wiadomosci", [])
        return sum(1 for m in msgs if m.get("unread", False))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        msgs = (self.coordinator.data or {}).get("wiadomosci", [])[:5]
        return {
            "wiadomosci": [
                {
                    "nadawca": m["author"],
                    "temat": m["title"],
                    "data": m["date"],
                    "nieprzeczytana": m.get("unread", False),
                    "jest_nowa": m.get("jest_nowa", False),
                    "ma_zalacznik": m.get("has_attachment", False),
                }
                for m in msgs
            ],
            "liczba_nieprzeczytanych": sum(1 for m in msgs if m.get("unread", False)),
            "sa_nowe_wiadomosci": any(m.get("jest_nowa", False) for m in msgs),
        }


# ---------------------------------------------------------------------------
# Plan lekcji, zadania domowe, frekwencja
# ---------------------------------------------------------------------------


class _LibrusSensor(CoordinatorEntity, SensorEntity):
    """Wspolna baza nowych czujnikow (opcjonalne odswiezanie stanu o polnocy).

    Koordynator odswieza dane co SCAN_INTERVAL, ale "dzis"/"jutro" zmienia sie
    o polnocy - dlatego te czujniki przeliczaja stan z cache'u po zmianie dnia.
    """

    _odswiez_o_polnocy = False

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
        name: str,
        unique_suffix: str,
        icon: str,
    ) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._odswiez_o_polnocy:
            self.async_on_remove(
                async_track_time_change(
                    self.hass, self._po_zmianie_dnia, hour=0, minute=0, second=10
                )
            )

    @callback
    def _po_zmianie_dnia(self, _now: datetime) -> None:
        self.async_write_ha_state()


class LibrusPlanLekcjiSensor(_LibrusSensor):
    """Plan lekcji na dzis albo na najblizszy nastepny dzien nauki."""

    _odswiez_o_polnocy = True
    _unrecorded_attributes = frozenset({"lekcje"})

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
        tryb: str,
    ) -> None:
        """tryb: "dzis" albo "nastepny"."""
        nazwa = "Plan lekcji dzis" if tryb == "dzis" else "Plan lekcji nastepny dzien"
        super().__init__(coordinator, config_entry, nazwa, f"plan_{tryb}", "mdi:timetable")
        self._tryb = tryb

    def _wybrany_dzien(self) -> Optional[tuple]:
        plan = self._data.get("plan") or {}
        if not plan:
            return None
        dzis = date.today()
        dzien = dzis if self._tryb == "dzis" else _nastepny_dzien_nauki(plan, dzis)
        if dzien is None:
            return None
        lekcje = plan.get(dzien.isoformat())
        if lekcje is None:
            return None
        return dzien, lekcje

    @property
    def native_value(self) -> Optional[int]:
        """Liczba (nieodwolanych) lekcji w wybranym dniu."""
        wybrany = self._wybrany_dzien()
        if wybrany is None:
            return None
        return sum(1 for l in wybrany[1] if not l["odwolana"])

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        wybrany = self._wybrany_dzien()
        if wybrany is None:
            return {}
        return _podsumowanie_dnia(*wybrany)


class LibrusPlanTygodniaSensor(_LibrusSensor):
    """Plan lekcji na caly tydzien (w weekend pokazuje nadchodzacy tydzien)."""

    _odswiez_o_polnocy = True
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, config_entry, "Plan lekcji tydzien", "plan_tydzien", "mdi:calendar-week"
        )

    def _tydzien(self) -> Dict[str, List[Dict[str, Any]]]:
        plan = self._data.get("plan") or {}
        poniedzialek = _poniedzialek_tygodnia_szkolnego(date.today())
        dni = [(poniedzialek + timedelta(days=i)).isoformat() for i in range(5)]
        return {iso: plan[iso] for iso in dni if iso in plan}

    @property
    def native_value(self) -> Optional[int]:
        """Liczba (nieodwolanych) lekcji w tygodniu."""
        tydzien = self._tydzien()
        if not tydzien:
            return None
        return sum(1 for lekcje in tydzien.values() for l in lekcje if not l["odwolana"])

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        tydzien = self._tydzien()
        if not tydzien:
            return {}
        return {
            "tydzien_od": min(tydzien),
            "tydzien_do": max(tydzien),
            "lekcji_wg_dni": {
                iso: sum(1 for l in lekcje if not l["odwolana"])
                for iso, lekcje in tydzien.items()
            },
            "liczba_zmian": sum(
                1 for lekcje in tydzien.values() for l in lekcje if l["zmiana"]
            ),
            "plan": tydzien,
        }


class LibrusZadaniaDomoweSensor(_LibrusSensor):
    """Zadania domowe z terminem w ciagu 7 dni (z trescia, jesli udalo sie pobrac)."""

    _odswiez_o_polnocy = True
    _unrecorded_attributes = frozenset({"zadania_7_dni"})

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, config_entry, "Zadania domowe", "zadania_domowe", "mdi:notebook-edit"
        )

    def _zadania_7_dni(self) -> List[Dict[str, Any]]:
        dzis = date.today()
        szczegoly = self._data.get("zadania_szczegoly") or {}
        wynik = []
        for z in self._data.get("zadania", []):
            termin = _parse_date(z.get("termin"))
            if termin is None or not 0 <= (termin - dzis).days <= 7:
                continue
            pozycja: Dict[str, Any] = {
                "przedmiot": z["przedmiot"],
                "kategoria": z["kategoria"],
                "nauczyciel": z["nauczyciel"],
                "lekcja": z["lekcja"],
                "data_zadania": z["data_zadania"],
                "termin": z["termin"],
                "dni_do_terminu": (termin - dzis).days,
            }
            det = szczegoly.get(z.get("href"))
            if det:
                tresc = _tresc_zadania(det)
                if tresc:
                    pozycja["tresc"] = tresc
                else:
                    pozycja["szczegoly"] = det
            wynik.append(pozycja)
        return sorted(wynik, key=lambda p: p["dni_do_terminu"])

    @property
    def native_value(self) -> int:
        """Liczba zadan z terminem w ciagu najblizszych 7 dni."""
        return len(self._zadania_7_dni())

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        zadania = self._zadania_7_dni()

        def _krotko(dni: int) -> List[str]:
            return [f"{z['przedmiot']} ({z['kategoria']})" for z in zadania if z["dni_do_terminu"] == dni]

        wg_przedmiotu: Dict[str, int] = {}
        for z in zadania:
            wg_przedmiotu[z["przedmiot"]] = wg_przedmiotu.get(z["przedmiot"], 0) + 1
        return {
            "liczba_na_dzis": len(_krotko(0)),
            "liczba_na_jutro": len(_krotko(1)),
            "na_dzis": _krotko(0),
            "na_jutro": _krotko(1),
            "najblizszy_termin": zadania[0]["termin"] if zadania else None,
            "wg_przedmiotu": wg_przedmiotu,
            "zadania_7_dni": zadania,
        }


class LibrusFrekwencjaSensor(_LibrusSensor):
    """Frekwencja w procentach (ogolem, osobno semestr 1 i 2)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Frekwencja", "frekwencja", "mdi:account-check")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> Optional[float]:
        freq = self._data.get("frekwencja")
        return freq["ogolem"] if freq else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        freq = self._data.get("frekwencja")
        if not freq:
            return {}
        sem = self._data.get("semestr_biezacy")
        return {
            "semestr_1": freq["semestr_1"],
            "semestr_2": freq["semestr_2"],
            "semestr_biezacy": sem,
            "frekwencja_semestru_biezacego": freq.get(f"semestr_{sem}"),
        }


class LibrusNieobecnosciSensor(_LibrusSensor):
    """Liczba nieusprawiedliwionych nieobecnosci w roku szkolnym + zestawienie wpisow."""

    _unrecorded_attributes = frozenset({"ostatnie", "nieusprawiedliwione"})

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, config_entry, "Nieobecnosci", "nieobecnosci", "mdi:account-alert"
        )

    @property
    def native_value(self) -> Optional[int]:
        if "obecnosc" not in self._data:
            return None
        return sum(1 for w in self._data["obecnosc"] if w["symbol"] == SYMBOL_NIEOBECNOSC)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        wpisy = self._data.get("obecnosc", [])
        wg_symbolu: Dict[str, int] = {}
        wg_semestru: Dict[str, Dict[str, int]] = {"semestr_1": {}, "semestr_2": {}}
        for w in wpisy:
            wg_symbolu[w["symbol"]] = wg_symbolu.get(w["symbol"], 0) + 1
            licznik = wg_semestru.setdefault(f"semestr_{w['semestr']}", {})
            licznik[w["symbol"]] = licznik.get(w["symbol"], 0) + 1

        bez_obecnosci = [w for w in wpisy if w["symbol"] != SYMBOL_OBECNOSC]
        nieuspr = [w for w in wpisy if w["symbol"] == SYMBOL_NIEOBECNOSC]
        return {
            "liczba_nieusprawiedliwionych": len(nieuspr),
            "liczba_usprawiedliwionych": wg_symbolu.get(SYMBOL_USPRAWIEDLIWIONA, 0),
            "liczba_spoznien": wg_symbolu.get(SYMBOL_SPOZNIENIE, 0),
            "liczba_zwolnien": wg_symbolu.get(SYMBOL_ZWOLNIENIE, 0),
            "wg_symbolu": wg_symbolu,
            "wg_semestru": wg_semestru,
            "sa_nowe_wpisy": any(
                _jest_nowa(w["data"]) for w in bez_obecnosci if w["symbol"] in SYMBOLE_ALERTU
            ),
            "nieusprawiedliwione": [_wpis_kompakt(w) for w in nieuspr[:20]],
            "ostatnie": [_wpis_kompakt(w) for w in bez_obecnosci[:10]],
        }
