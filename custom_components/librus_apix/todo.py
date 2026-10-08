"""Lista terminow zajec dodatkowych: odznaczenie terminu na liscie odwoluje go w planie (i odwrotnie)."""
from datetime import date
from typing import List

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
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
    async_add_entities([LibrusTerminyZajec(coordinator, config_entry)])


class LibrusTerminyZajec(LibrusEntityMixin, CoordinatorEntity, TodoListEntity):
    """Terminy zajec dodatkowych z tygodnia pokazanego w planie (podaza za stronicowaniem); zaznaczony termin = odwolany."""

    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM
    _odswiez_o_polnocy = True

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._init_librus(config_entry, "Zajęcia dodatkowe - terminy", "dodatkowa_terminy", "mdi:calendar-check")

    @property
    def todo_items(self) -> List[TodoItem]:
        return [
            TodoItem(
                uid=t["klucz"],
                # sekcja "Ukonczone" to wbudowany napis karty HA - dlatego odwolanie opisujemy tez w tresci pozycji
                summary=f"{t['przedmiot']} · {t['od']}–{t['do']}" + (" · odwołane" if t["odwolana"] else ""),
                status=TodoItemStatus.COMPLETED if t["odwolana"] else TodoItemStatus.NEEDS_ACTION,
                due=date.fromisoformat(t["data"]),
                description=t["miejsce"] or None,
            )
            for t in self.coordinator.terminy_dodatkowych()
        ]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Zmiana statusu = odwolanie (zaznaczone) albo przywrocenie (odznaczone) jednego terminu."""
        try:
            id_, data = (item.uid or "").split("|")
            await self.coordinator.async_ustaw_odwolanie_terminu(
                id_, data, item.status == TodoItemStatus.COMPLETED
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
