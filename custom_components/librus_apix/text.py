"""Pola tekstowe formularza "Lekcja dodatkowa" (nazwa zajec i miejsce)."""
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator, LibrusEntityMixin


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][config_entry.entry_id]
    async_add_entities([
        LibrusPoleTekstowe(coordinator, config_entry, "przedmiot", "Lekcja dodatkowa - nazwa", "mdi:form-textbox"),
        LibrusPoleTekstowe(coordinator, config_entry, "miejsce", "Lekcja dodatkowa - miejsce", "mdi:map-marker"),
    ])


class LibrusPoleTekstowe(LibrusEntityMixin, CoordinatorEntity, TextEntity):
    """Pole formularza przechowywane w koordynatorze (nie w Librusie)."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 60

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, pole: str, nazwa: str, ikona: str) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, nazwa, f"dodatkowa_{pole}", ikona)
        self._pole = pole

    @property
    def native_value(self) -> str:
        return self.coordinator.formularz.get(self._pole) or ""

    async def async_set_value(self, value: str) -> None:
        self.coordinator.ustaw_formularz(self._pole, value)
