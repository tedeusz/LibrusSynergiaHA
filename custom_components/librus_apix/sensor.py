"""Sensory Librus APIX."""
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import (
    SYMBOL_NIEOBECNOSC,
    SYMBOL_OBECNOSC,
    SYMBOL_SPOZNIENIE,
    SYMBOL_USPRAWIEDLIWIONA,
    SYMBOL_ZWOLNIENIE,
    SYMBOLE_ALERTU,
    LibrusDataUpdateCoordinator,
    LibrusEntityMixin,
    _aktualna_lekcja,
    _device_info,
    _dzis,
    _granice_dnia,
    _jest_nowa,
    _nastepna_lekcja,
    _nastepny_dzien_nauki,
    _nazwa_lekcji,
    _parse_date,
    _podsumowanie_dnia,
    _poniedzialek_tygodnia_szkolnego,
    _srednia,
    _srednia_ocen,
    _srednia_wazona,
    _suma_wag,
    _tematy_wg_dni,
    _tresc_zadania,
    _wpis_kompakt,
)

_LOGGER = logging.getLogger(__name__)

MAX_OGLOSZEN_W_ATRYBUTACH = 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Konfiguracja platformy czujnikow Librus APIX."""
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][
        config_entry.entry_id
    ]

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
        LibrusOgloszeniaSensor(coordinator, config_entry),
        LibrusTematyLekcjiSensor(coordinator, config_entry),
        LibrusCzasLekcjiSensor(coordinator, config_entry, "poczatek", "dzis"),
        LibrusCzasLekcjiSensor(coordinator, config_entry, "koniec", "dzis"),
        LibrusCzasLekcjiSensor(coordinator, config_entry, "poczatek", "nastepny"),
        LibrusAktualnaLekcjaSensor(coordinator, config_entry),
    ]

    # Tworz czujniki per przedmiot na podstawie pierwszego pobrania danych
    for subject in coordinator.data.get("oceny_wg_przedmiotu", {}).keys():
        entities.append(LibrusPrzedmiotSensor(coordinator, subject, config_entry))
        entities.append(LibrusSredniaPrzedmiotuSensor(coordinator, subject, config_entry))

    # Czujnik globalnej sredniej
    entities.append(LibrusSredniaOcenSensor(coordinator, config_entry))

    async_add_entities(entities)


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

        srednia = _srednia(oceny)

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
        return _srednia(wszystkie)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        wg_przedmiotow = data.get("oceny_wg_przedmiotu", {})
        srednie_przedmiotow = {
            subject: _srednia(oceny)
            for subject, oceny in wg_przedmiotow.items()
            if _srednia(oceny) is not None
        }
        wszystkie = [g for oceny in wg_przedmiotow.values() for g in oceny]
        ze_srednich = [
            _srednia_wazona(oceny) for oceny in wg_przedmiotow.values()
            if _srednia_wazona(oceny) is not None
        ]
        return {
            "srednie_wg_przedmiotow": srednie_przedmiotow,
            "srednia_wazona": _srednia_wazona(wszystkie),
            "srednia_arytmetyczna": _srednia_ocen(wszystkie),
            "srednia_ze_srednich_przedmiotow": (
                round(sum(ze_srednich) / len(ze_srednich), 2) if ze_srednich else None
            ),
            "srednie_librus": data.get("srednie_librus", {}),
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
        return _srednia(oceny)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        return {
            "przedmiot": self._subject,
            "lista_ocen": ", ".join(g["ocena"] for g in oceny),
            "liczba_ocen": len(oceny),
            "srednia_wazona": _srednia_wazona(oceny),
            "srednia_arytmetyczna": _srednia_ocen(oceny),
            "suma_wag": _suma_wag(oceny),
            "srednia_librus": (self.coordinator.data or {}).get("srednie_librus", {}).get(self._subject),
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
    """Czujnik z wiadomosciami (nadawca, temat, a dla nowych wiadomosci takze tresc)."""

    _unrecorded_attributes = frozenset({"wiadomosci", "otwarta", "przegladanie"})

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
                    "tresc": (m.get("tresc") or "")[:1000],
                }
                for m in msgs
            ],
            "liczba_nieprzeczytanych": sum(1 for m in msgs if m.get("unread", False)),
            "sa_nowe_wiadomosci": any(m.get("jest_nowa", False) for m in msgs),
            # Ostatnio otwarta (kliknieta) wiadomosc z trescia - patrz usluga librus_apix.pobierz_tresc
            "otwarta": self.coordinator._otwarta or {},
            # Aktualny ekran przegladania (usluga librus_apix.przegladaj); domyslnie najnowsze
            "przegladanie": {
                **self.coordinator.opis_widoku(),
                "wiadomosci": [
                    {
                        "nadawca": m["author"],
                        "temat": m["title"],
                        "data": m["date"],
                        "nieprzeczytana": m.get("unread", False),
                        "jest_nowa": m.get("jest_nowa", False),
                        "ma_zalacznik": m.get("has_attachment", False),
                    }
                    for m in self.coordinator.widok()
                ],
            },
        }


# ---------------------------------------------------------------------------
# Plan lekcji, zadania domowe, frekwencja
# ---------------------------------------------------------------------------


class _LibrusSensor(LibrusEntityMixin, CoordinatorEntity, SensorEntity):
    """Wspolna baza nowych czujnikow (patrz LibrusEntityMixin)."""

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
        self._init_librus(config_entry, name, unique_suffix, icon)


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
        dzis = _dzis()
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
        poniedzialek = _poniedzialek_tygodnia_szkolnego(_dzis())
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
        dzis = _dzis()
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


class LibrusOgloszeniaSensor(_LibrusSensor):
    """Ogloszenia szkoly (stan = liczba ogloszen na liscie Librusa)."""

    _unrecorded_attributes = frozenset({"ogloszenia", "przegladanie"})

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Ogloszenia", "ogloszenia", "mdi:bullhorn")

    @property
    def native_value(self) -> Optional[int]:
        if "ogloszenia" not in self._data:
            return None
        return len(self._data["ogloszenia"])

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        lista = self._data.get("ogloszenia", [])
        return {
            "ostatnie_ogloszenie": lista[0]["tytul"] if lista else None,
            "sa_nowe": any(o["jest_nowe"] for o in lista),
            "ogloszenia": [
                {**o, "tresc": o["tresc"][:500]} for o in lista[:MAX_OGLOSZEN_W_ATRYBUTACH]
            ],
            # Aktualny ekran przegladania (usluga librus_apix.przegladaj_ogloszenia) - cala lista
            "przegladanie": (
                lambda w: {**w, "ogloszenia": [{**o, "tresc": o["tresc"][:600]} for o in w["ogloszenia"]]}
            )(self.coordinator.widok_ogloszen()),
        }


class LibrusTematyLekcjiSensor(_LibrusSensor):
    """Tematy zrealizowanych lekcji (stan = liczba dzisiejszych lekcji z wpisanym tematem)."""

    _odswiez_o_polnocy = True
    _unrecorded_attributes = frozenset({"dzis", "ostatni_dzien_tematy", "wg_dni"})

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, config_entry, "Tematy lekcji", "tematy_lekcji", "mdi:book-open-page-variant"
        )

    @property
    def native_value(self) -> Optional[int]:
        if "tematy" not in self._data:
            return None
        return len(_tematy_wg_dni(self._data["tematy"]).get(_dzis().isoformat(), []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        wg_dni = _tematy_wg_dni(self._data.get("tematy", []))
        ostatni = max(wg_dni) if wg_dni else None
        return {
            "dzis": wg_dni.get(_dzis().isoformat(), []),
            "ostatni_dzien": ostatni,
            "ostatni_dzien_tematy": wg_dni.get(ostatni, []) if ostatni else [],
            "wg_dni": wg_dni,
        }


class LibrusCzasLekcjiSensor(_LibrusSensor):
    """Znacznik czasu poczatku/konca lekcji - do wyzwalaczy `time` w automatyzacjach."""

    _odswiez_o_polnocy = True
    _OPISY = {
        ("poczatek", "dzis"): ("Poczatek lekcji dzis", "mdi:clock-start"),
        ("koniec", "dzis"): ("Koniec lekcji dzis", "mdi:clock-end"),
        ("poczatek", "nastepny"): ("Poczatek lekcji nastepny dzien", "mdi:alarm"),
    }

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, co: str, tryb: str
    ) -> None:
        """co: "poczatek"/"koniec"; tryb: "dzis"/"nastepny" (najblizszy dzien nauki)."""
        nazwa, ikona = self._OPISY[(co, tryb)]
        super().__init__(coordinator, config_entry, nazwa, f"czas_{co}_{tryb}", ikona)
        self._co = co
        self._tryb = tryb
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    def _dzien(self) -> Optional[date]:
        dzis = _dzis()
        return dzis if self._tryb == "dzis" else _nastepny_dzien_nauki(self._plan, dzis)

    @property
    def native_value(self) -> Optional[datetime]:
        dzien = self._dzien()
        if dzien is None:
            return None
        lekcje = self._plan.get(dzien.isoformat())
        if lekcje is None:
            return None
        start, koniec = _granice_dnia(lekcje, dzien)
        return start if self._co == "poczatek" else koniec

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        dzien = self._dzien()
        return {"dzien": dzien.isoformat()} if dzien else {}


class LibrusAktualnaLekcjaSensor(_LibrusSensor):
    """Nazwa trwajacej lekcji (lub brak stanu), z informacja o nastepnej lekcji."""

    _odswiez_co_minute = True

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Aktualna lekcja", "aktualna_lekcja", "mdi:school")

    @property
    def native_value(self) -> Optional[str]:
        lekcja = _aktualna_lekcja(self._plan, dt_util.now())
        return _nazwa_lekcji(lekcja) if lekcja else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        teraz = dt_util.now()
        attrs: Dict[str, Any] = {}
        lekcja = _aktualna_lekcja(self._plan, teraz)
        if lekcja:
            attrs.update({
                "numer": lekcja["numer"], "od": lekcja["od"], "do": lekcja["do"],
                "nauczyciel_sala": lekcja["nauczyciel_sala"], "zmiana": lekcja["zmiana"],
            })
            if lekcja.get("zastepca"):
                attrs["zastepca"] = lekcja["zastepca"]
        nastepna = _nastepna_lekcja(self._plan, teraz)
        attrs["nastepna_lekcja"] = _nazwa_lekcji(nastepna) if nastepna else None
        attrs["nastepna_od"] = nastepna["od"] if nastepna else None
        return attrs
