"""Daty formularza "Lekcja dodatkowa": data zajec oraz opcjonalny zakres obowiazywania."""
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
    async_add_entities([
        LibrusData(coordinator, config_entry),
        LibrusDataZakresu(coordinator, config_entry, "wazne_od", "Lekcja dodatkowa - obowiązuje od", "obowiazuje_od"),
        LibrusDataZakresu(coordinator, config_entry, "wazne_do", "Lekcja dodatkowa - obowiązuje do", "obowiazuje_do"),
    ])


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


class LibrusDataZakresu(LibrusEntityMixin, CoordinatorEntity, DateEntity):
    """Opcjonalna granica obowiazywania zajec cyklicznych ("od" / "do" wlacznie); pusta = bez ograniczenia."""

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, pole: str, nazwa: str, klucz: str
    ) -> None:
        super().__init__(coordinator)
        self._pole = pole
        self._init_librus(config_entry, nazwa, f"dodatkowa_{klucz}", "mdi:calendar-range")

    @property
    def native_value(self) -> data_ | None:
        return self.coordinator.formularz.get(self._pole)

    async def async_set_value(self, value: data_) -> None:
        self.coordinator.ustaw_formularz(self._pole, value)
