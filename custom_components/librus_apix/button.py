"""Przyciski formularza "Lekcja dodatkowa": dodaj, zapisz zmiany i usun."""
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator, LibrusEntityMixin


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][config_entry.entry_id]
    async_add_entities([
        LibrusPrzyciskDodaj(coordinator, config_entry),
        LibrusPrzyciskZapisz(coordinator, config_entry),
        LibrusPrzyciskUsun(coordinator, config_entry),
    ])


class _Przycisk(LibrusEntityMixin, CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, nazwa: str, sufiks: str, ikona: str) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, nazwa, f"dodatkowa_{sufiks}", ikona)


class LibrusPrzyciskDodaj(_Przycisk):
    """Dodaje lekcje z pol formularza; bledne dane to czytelny komunikat w interfejsie."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - dodaj", "dodaj", "mdi:plus-circle")

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_dodaj_z_formularza()
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err


class LibrusPrzyciskUsun(_Przycisk):
    """Usuwa lekcje wybrana na liscie "do usuniecia"."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - usuń", "usun", "mdi:delete")

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_usun_wybrana()
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err


class LibrusPrzyciskZapisz(_Przycisk):
    """Zapisuje zmiany zajec wczytanych do formularza (wybranych na liscie "edycja")."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - zapisz zmiany", "zapisz", "mdi:content-save")

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_zapisz_z_formularza()
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
