"""Czujniki binarne Librus APIX (lekcje dzis/jutro, trwajaca lekcja)."""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import (
    LibrusDataUpdateCoordinator,
    LibrusEntityMixin,
    _aktualna_lekcja,
    _dzis,
    _nazwa_lekcji,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Konfiguracja platformy czujnikow binarnych."""
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][
        config_entry.entry_id
    ]
    async_add_entities([
        LibrusLekcjeDniaBinarySensor(coordinator, config_entry, "dzis"),
        LibrusLekcjeDniaBinarySensor(coordinator, config_entry, "jutro"),
        LibrusLekcjaTrwaBinarySensor(coordinator, config_entry),
    ])


class _LibrusBinarySensor(LibrusEntityMixin, CoordinatorEntity, BinarySensorEntity):
    """Wspolna baza czujnikow binarnych."""

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


class LibrusLekcjeDniaBinarySensor(_LibrusBinarySensor):
    """Czy dzis / jutro sa jakiekolwiek (nieodwolane) lekcje wg planu."""

    _odswiez_o_polnocy = True

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, dzien: str
    ) -> None:
        """dzien: "dzis" albo "jutro"."""
        nazwa = "Dzis sa lekcje" if dzien == "dzis" else "Szkola jutro"
        ikona = "mdi:school" if dzien == "dzis" else "mdi:school-outline"
        super().__init__(coordinator, config_entry, nazwa, f"lekcje_{dzien}", ikona)
        self._dzien = dzien

    def _lekcje(self) -> Optional[List[Dict[str, Any]]]:
        dzien = _dzis() + timedelta(days=0 if self._dzien == "dzis" else 1)
        lekcje = self._plan.get(dzien.isoformat())
        return None if lekcje is None else [l for l in lekcje if not l["odwolana"]]

    @property
    def is_on(self) -> Optional[bool]:
        lekcje = self._lekcje()
        return None if lekcje is None else len(lekcje) > 0

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        lekcje = self._lekcje()
        return {} if lekcje is None else {"liczba_lekcji": len(lekcje)}


class LibrusLekcjaTrwaBinarySensor(_LibrusBinarySensor):
    """Czy w tej chwili trwa lekcja (nie przerwa, nie odwolana)."""

    _odswiez_co_minute = True

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja trwa", "lekcja_trwa", "mdi:bell-ring")

    @property
    def is_on(self) -> Optional[bool]:
        if not self._plan:
            return None
        return _aktualna_lekcja(self._plan, dt_util.now()) is not None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        lekcja = _aktualna_lekcja(self._plan, dt_util.now()) if self._plan else None
        return {"przedmiot": _nazwa_lekcji(lekcja), "do": lekcja["do"]} if lekcja else {}
