"""Kalendarze Librus APIX: plan lekcji oraz terminarz z terminami zadan domowych."""
from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional, Tuple

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import (
    LibrusDataUpdateCoordinator,
    LibrusEntityMixin,
    _na_czas,
    _nazwa_lekcji,
    _parse_date,
    _tresc_zadania,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Konfiguracja platformy kalendarzy."""
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][
        config_entry.entry_id
    ]
    async_add_entities([
        LibrusPlanLekcjiCalendar(coordinator, config_entry),
        LibrusTerminarzCalendar(coordinator, config_entry),
    ])


def _zakres(ev: CalendarEvent) -> Tuple[datetime, datetime]:
    """Poczatek i koniec wydarzenia jako datetime ze strefa (calodniowe: od 00:00)."""
    tz = dt_util.DEFAULT_TIME_ZONE

    def _dt(v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        return datetime.combine(v, time.min, tzinfo=tz)

    return _dt(ev.start), _dt(ev.end)


class _LibrusCalendar(LibrusEntityMixin, CoordinatorEntity, CalendarEntity):
    """Wspolna logika kalendarzy: wydarzenia budowane z cache'u koordynatora."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
        name: str,
        unique_suffix: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, name, unique_suffix, icon)

    def _wydarzenia(self) -> List[CalendarEvent]:
        """Wszystkie wydarzenia, ktore moga byc 'aktualne' (bez odwolanych)."""
        raise NotImplementedError

    def _wydarzenia_do_listy(self) -> List[CalendarEvent]:
        """Wydarzenia pokazywane w widoku kalendarza (domyslnie jak _wydarzenia)."""
        return self._wydarzenia()

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Trwajace teraz wydarzenie albo najblizsze nadchodzace."""
        teraz = dt_util.now()
        kandydaci = []
        for ev in self._wydarzenia():
            start, koniec = _zakres(ev)
            if koniec > teraz:
                kandydaci.append((start, ev))
        return min(kandydaci, key=lambda x: x[0])[1] if kandydaci else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> List[CalendarEvent]:
        """Wydarzenia zachodzace na przedzial [start_date, end_date)."""
        wynik = []
        for ev in self._wydarzenia_do_listy():
            start, koniec = _zakres(ev)
            if start < end_date and koniec > start_date:
                wynik.append(ev)
        return sorted(wynik, key=lambda e: _zakres(e)[0])


class LibrusPlanLekcjiCalendar(_LibrusCalendar):
    """Plan lekcji jako kalendarz (widok tygodnia; biezacy i nastepny tydzien)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, config_entry, "Plan lekcji kalendarz", "kalendarz_plan", "mdi:calendar-clock"
        )

    def _zbierz(self, z_odwolanymi: bool) -> List[CalendarEvent]:
        wynik = []
        for iso, lekcje in (self._data.get("plan") or {}).items():
            for l in lekcje:
                if l["odwolana"] and not z_odwolanymi:
                    continue
                start, koniec = _na_czas(iso, l["od"]), _na_czas(iso, l["do"])
                if not start or not koniec:
                    continue
                nazwa = _nazwa_lekcji(l)
                dodatkowa = bool(l.get("dodatkowa"))
                if dodatkowa:
                    nazwa = f"➕ {nazwa}"
                if l["odwolana"]:
                    nazwa = f"❌ {nazwa} (odwołana)"
                elif l["zmiana"]:
                    nazwa = f"🔄 {nazwa} ({l['zmiana']})"
                opis = ["Zajęcia dodatkowe" if dodatkowa else f"{l['numer']}. lekcja", l["nauczyciel_sala"]]
                if l.get("zastepca"):
                    opis.append(f"Zastępstwo: {l['zastepca']}")
                if l.get("sala_zastepcza"):
                    opis.append(f"Sala: {l['sala_zastepcza']}")
                wynik.append(CalendarEvent(
                    start=start, end=koniec, summary=nazwa,
                    description="\n".join(x for x in opis if x),
                    uid=f"plan-{iso}-{l['id_dodatkowej'] if dodatkowa else l['numer']}",
                ))
        return wynik

    def _wydarzenia(self) -> List[CalendarEvent]:
        return self._zbierz(z_odwolanymi=False)

    def _wydarzenia_do_listy(self) -> List[CalendarEvent]:
        return self._zbierz(z_odwolanymi=True)


class LibrusTerminarzCalendar(_LibrusCalendar):
    """Terminarz Librusa (sprawdziany itp.) oraz terminy oddania zadan domowych - calodniowe."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, config_entry, "Terminarz i zadania", "kalendarz_terminarz", "mdi:calendar-star"
        )

    @staticmethod
    def _calodniowe(dzien: date, **kw: Any) -> CalendarEvent:
        return CalendarEvent(start=dzien, end=dzien + timedelta(days=1), **kw)

    def _wydarzenia(self) -> List[CalendarEvent]:
        wynik: List[CalendarEvent] = []
        for i, z in enumerate(self._data.get("terminarz", [])):
            dzien = _parse_date(z.get("data"))
            if dzien is None:
                continue
            tytul, przedmiot = z.get("tytul", ""), z.get("przedmiot", "")
            opis = []
            if z.get("numer_lekcji"):
                opis.append(f"Lekcja: {z['numer_lekcji']}")
            for k, v in (z.get("szczegoly") or {}).items():
                if v and str(v).lower() != "unknown":
                    opis.append(f"{k}: {v}")
            wynik.append(self._calodniowe(
                dzien,
                summary=f"{tytul}: {przedmiot}" if przedmiot else tytul,
                description="\n".join(opis) or None,
                uid=f"terminarz-{z.get('href') or i}",
            ))

        szczegoly = self._data.get("zadania_szczegoly") or {}
        for i, z in enumerate(self._data.get("zadania", [])):
            dzien = _parse_date(z.get("termin"))
            if dzien is None:
                continue
            opis = [f"Nauczyciel: {z['nauczyciel']}"] if z.get("nauczyciel") else []
            tresc = _tresc_zadania(szczegoly.get(z.get("href")))
            if tresc:
                opis.append(tresc)
            wynik.append(self._calodniowe(
                dzien,
                summary=f"{z['kategoria']}: {z['przedmiot']}",
                description="\n".join(opis) or None,
                uid=f"zadanie-{z.get('href') or i}",
            ))
        return wynik
