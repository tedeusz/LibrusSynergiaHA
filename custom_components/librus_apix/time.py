"""Godziny formularza "Lekcja dodatkowa" (od, do)."""
from datetime import time as czas

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator, LibrusEntityMixin
from .lekcje_dodatkowe import minuty


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][config_entry.entry_id]
    async_add_entities([
        LibrusGodzina(coordinator, config_entry, "od", "Lekcja dodatkowa - od", "mdi:clock-start"),
        LibrusGodzina(coordinator, config_entry, "do", "Lekcja dodatkowa - do", "mdi:clock-end"),
    ])


class LibrusGodzina(LibrusEntityMixin, CoordinatorEntity, TimeEntity):
    """Godzina rozpoczecia albo zakonczenia (formularz w koordynatorze, zapis jako "HH:MM")."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, pole: str, nazwa: str, ikona: str) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, nazwa, f"dodatkowa_{pole}", ikona)
        self._pole = pole

    @property
    def native_value(self) -> czas | None:
        m = minuty(self.coordinator.formularz.get(self._pole))
        return czas(m // 60, m % 60) if m is not None else None

    async def async_set_value(self, value: czas) -> None:
        self.coordinator.ustaw_formularz(self._pole, f"{value.hour:02d}:{value.minute:02d}")
