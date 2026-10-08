"""Data formularza "Lekcja dodatkowa" (tylko dla zajec jednorazowych)."""
from datetime import date as data_

from homeassistant.components.date import DateEntity
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
    async_add_entities([LibrusData(coordinator, config_entry)])


class LibrusData(LibrusEntityMixin, CoordinatorEntity, DateEntity):
    """Data zajec jednorazowych (dla cotygodniowych nie ma znaczenia)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, "Lekcja dodatkowa - data", "dodatkowa_data", "mdi:calendar-today")

    @property
    def native_value(self) -> data_ | None:
        return self.coordinator.formularz.get("data")

    async def async_set_value(self, value: data_) -> None:
        self.coordinator.ustaw_formularz("data", value)
