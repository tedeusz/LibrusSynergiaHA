"""Listy wyboru formularza "Lekcja dodatkowa": powtarzanie, dzien tygodnia, edycja, usuwanie i odwolywanie terminu."""
from typing import List, Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator, LibrusEntityMixin
from .lekcje_dodatkowe import COTYGODNIOWO, DNI_TYGODNIA, JEDNORAZOWO, etykiety, etykiety_terminow

POWTARZANIE = {COTYGODNIOWO: "co tydzień", JEDNORAZOWO: "jednorazowo"}
# Pierwsza opcja list: stan "nic nie wybrano" (zamiast "unknown")
NIC_NIE_WYBRANO = "— wybierz —"
NOWE_ZAJECIA = "➕ nowe zajęcia"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LibrusDataUpdateCoordinator = hass.data[DOMAIN]["coordinators"][config_entry.entry_id]
    async_add_entities([
        LibrusWyborPowtarzania(coordinator, config_entry),
        LibrusWyborDnia(coordinator, config_entry),
        LibrusWyborDoUsuniecia(coordinator, config_entry),
        LibrusWyborEdycji(coordinator, config_entry),
        LibrusWyborTerminu(coordinator, config_entry),
    ])


class _Wybor(LibrusEntityMixin, CoordinatorEntity, SelectEntity):
    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry, nazwa: str, sufiks: str, ikona: str) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, nazwa, f"dodatkowa_{sufiks}", ikona)


class LibrusWyborPowtarzania(_Wybor):
    """Czy zajecia powtarzaja sie co tydzien, czy odbeda sie raz."""

    _attr_options = list(POWTARZANIE.values())

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - powtarzanie", "powtarzanie", "mdi:repeat")

    @property
    def current_option(self) -> Optional[str]:
        return POWTARZANIE.get(self.coordinator.formularz.get("powtarzanie"))

    async def async_select_option(self, option: str) -> None:
        klucz = next((k for k, v in POWTARZANIE.items() if v == option), None)
        if klucz is not None:
            self.coordinator.ustaw_formularz("powtarzanie", klucz)


class LibrusWyborDnia(_Wybor):
    """Dzien tygodnia zajec cotygodniowych."""

    _attr_options = list(DNI_TYGODNIA)

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - dzień", "dzien", "mdi:calendar-week")

    @property
    def current_option(self) -> Optional[str]:
        dzien = self.coordinator.formularz.get("dzien")
        return DNI_TYGODNIA[dzien] if isinstance(dzien, int) and 0 <= dzien < 7 else None

    async def async_select_option(self, option: str) -> None:
        if option in DNI_TYGODNIA:
            self.coordinator.ustaw_formularz("dzien", DNI_TYGODNIA.index(option))


class LibrusWyborDoUsuniecia(_Wybor):
    """Zdefiniowane lekcje dodatkowe; wybrana jest usuwana przyciskiem "usuń"."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - do usunięcia", "do_usuniecia", "mdi:playlist-remove")

    @property
    def options(self) -> List[str]:
        return [NIC_NIE_WYBRANO, *etykiety(self.coordinator.lekcje_dodatkowe()).values()]

    @property
    def current_option(self) -> Optional[str]:
        return etykiety(self.coordinator.lekcje_dodatkowe()).get(self.coordinator.do_usuniecia, NIC_NIE_WYBRANO)

    async def async_select_option(self, option: str) -> None:
        self.coordinator.do_usuniecia = next(
            (id_ for id_, napis in etykiety(self.coordinator.lekcje_dodatkowe()).items() if napis == option), None
        )
        self.coordinator.async_update_listeners()


class LibrusWyborEdycji(_Wybor):
    """Wybor zajec do edycji: wczytuje je do formularza; "nowe zajecia" czysci formularz i wraca do dodawania."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - edycja", "edycja", "mdi:pencil")

    @property
    def options(self) -> List[str]:
        return [NOWE_ZAJECIA, *etykiety(self.coordinator.lekcje_dodatkowe()).values()]

    @property
    def current_option(self) -> Optional[str]:
        return etykiety(self.coordinator.lekcje_dodatkowe()).get(self.coordinator.edytowana, NOWE_ZAJECIA)

    async def async_select_option(self, option: str) -> None:
        for id_, napis in etykiety(self.coordinator.lekcje_dodatkowe()).items():
            if napis == option:
                self.coordinator.wczytaj_do_formularza(id_)
                return
        self.coordinator.wyczysc_formularz()


class LibrusWyborTerminu(_Wybor):
    """Najblizsze terminy zajec dodatkowych (z planu); wybrany odwolujesz albo przywracasz przyciskami."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "Lekcja dodatkowa - termin", "termin", "mdi:calendar-cursor")

    def _etykiety(self) -> dict:
        return etykiety_terminow(self.coordinator.terminy_dodatkowych())

    @property
    def options(self) -> List[str]:
        return [NIC_NIE_WYBRANO, *self._etykiety().values()]

    @property
    def current_option(self) -> Optional[str]:
        return self._etykiety().get(self.coordinator.termin, NIC_NIE_WYBRANO)

    async def async_select_option(self, option: str) -> None:
        self.coordinator.termin = next((k for k, napis in self._etykiety().items() if napis == option), None)
        self.coordinator.async_update_listeners()
