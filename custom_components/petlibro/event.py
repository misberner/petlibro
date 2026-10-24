"""Support for PETLIBRO event entities (feeding / pet activity from work records)."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .hub import PetLibroHub
from .devices import Device
from .devices.event import EVENT_UPDATE
from .devices.work_records import (
    FEEDING_EVENT_TYPES,
    PET_VISIT_EVENT_TYPES,
    feeding_event_type,
    is_feeding_record,
    is_pet_visit_record,
    parse_params,
    pet_visit_duration,
    pet_visit_event_type,
)
from .devices.feeders.air_smart_feeder import AirSmartFeeder
from .devices.feeders.granary_smart_feeder import GranarySmartFeeder
from .devices.feeders.granary_smart_camera_feeder import GranarySmartCameraFeeder
from .devices.feeders.one_rfid_smart_feeder import OneRFIDSmartFeeder
from .devices.feeders.polar_wet_food_feeder import PolarWetFoodFeeder
from .devices.feeders.space_smart_feeder import SpaceSmartFeeder
from .entity import PetLibroEntity, _DeviceT, PetLibroEntityDescription

_LOGGER = logging.getLogger(__name__)

ATTR_RECORD_TIME = "record_time"

# Number of record IDs remembered per entity to avoid firing the same record twice.
MAX_SEEN_RECORDS = 200


def _base_attributes(record: dict) -> dict[str, Any]:
    return {
        "record_id": record["id"],
        ATTR_RECORD_TIME: dt_util.utc_from_timestamp(record["recordTime"] / 1000).isoformat(),
        "record_type": record.get("type"),
        "event_code": record.get("eventType"),
        "message": record.get("content"),
    }


def _feeding_attributes(record: dict) -> dict[str, Any]:
    attributes = _base_attributes(record)
    params = parse_params(record)
    if (plate := params.get("plate")) is not None:
        attributes["plate"] = plate
    if label := params.get("label") or record.get("label"):
        attributes["label"] = label
    if isinstance(portions := record.get("actualGrainNum"), int):
        attributes["portions"] = portions
    return attributes


def _pet_visit_attributes(record: dict) -> dict[str, Any]:
    attributes = _base_attributes(record)
    if (duration := pet_visit_duration(record)) is not None:
        attributes["duration"] = duration
    return attributes


@dataclass(frozen=True)
class PetLibroEventEntityDescription(EventEntityDescription, PetLibroEntityDescription[_DeviceT]):
    """A class that describes device event entities backed by work records."""

    record_filter: Callable[[dict], bool] = lambda _: False
    event_type_fn: Callable[[dict], str] = lambda _: ""
    attributes_fn: Callable[[dict], dict[str, Any]] = _base_attributes


class PetLibroEventEntity(PetLibroEntity[_DeviceT], EventEntity):
    """PETLIBRO event entity that fires once per new matching work record."""

    entity_description: PetLibroEventEntityDescription[_DeviceT]

    def __init__(self, device, hub, description) -> None:
        super().__init__(device, hub, description)
        self._seen_record_ids: dict[str, None] = {}
        self._last_record_time: int | None = None
        self._baseline_done = False

    async def async_added_to_hass(self) -> None:
        """Restore the last fired record time and start processing records."""
        await super().async_added_to_hass()
        # EventEntity has already restored its last event at this point.
        if restored := self.state_attributes.get(ATTR_RECORD_TIME):
            if restored_time := dt_util.parse_datetime(restored):
                self._last_record_time = int(restored_time.timestamp() * 1000)
        self.async_on_remove(self.device.on(EVENT_UPDATE, self._process_records))
        self._process_records()

    @callback
    def _process_records(self) -> None:
        records = [
            record for record in self.device.work_records
            if self.entity_description.record_filter(record)
        ]
        if not records:
            return

        if not self._baseline_done:
            # First batch after startup: only fire records newer than the last one we fired
            # before the restart. On a fresh install nothing is fired for historic records.
            self._baseline_done = True
            if self._last_record_time is None:
                new_records = []
            else:
                new_records = [r for r in records if r["recordTime"] > self._last_record_time]
        else:
            new_records = [r for r in records if r["id"] not in self._seen_record_ids]

        for record in records:
            self._seen_record_ids[record["id"]] = None
        while len(self._seen_record_ids) > MAX_SEEN_RECORDS:
            self._seen_record_ids.pop(next(iter(self._seen_record_ids)))

        for record in new_records:
            event_type = self.entity_description.event_type_fn(record)
            _LOGGER.debug("Firing %s event %s for %s: %s", self.key, event_type, self.device.name, record)
            self._trigger_event(event_type, self.entity_description.attributes_fn(record))
            self._last_record_time = max(self._last_record_time or 0, record["recordTime"])
            self.async_write_ha_state()


def _feeding_description(device_type: type[_DeviceT]) -> PetLibroEventEntityDescription[_DeviceT]:
    return PetLibroEventEntityDescription[device_type](
        key="feeding_event",
        translation_key="feeding_event",
        icon="mdi:bowl-mix",
        name="Feeding",
        event_types=FEEDING_EVENT_TYPES,
        record_filter=is_feeding_record,
        event_type_fn=feeding_event_type,
        attributes_fn=_feeding_attributes,
    )


DEVICE_EVENT_MAP: dict[type[Device], list[PetLibroEventEntityDescription]] = {
    AirSmartFeeder: [_feeding_description(AirSmartFeeder)],
    GranarySmartFeeder: [_feeding_description(GranarySmartFeeder)],
    GranarySmartCameraFeeder: [_feeding_description(GranarySmartCameraFeeder)],
    OneRFIDSmartFeeder: [_feeding_description(OneRFIDSmartFeeder)],
    SpaceSmartFeeder: [_feeding_description(SpaceSmartFeeder)],
    PolarWetFoodFeeder: [
        _feeding_description(PolarWetFoodFeeder),
        PetLibroEventEntityDescription[PolarWetFoodFeeder](
            key="pet_visit_event",
            translation_key="pet_visit_event",
            icon="mdi:paw",
            name="Pet Visit",
            event_types=PET_VISIT_EVENT_TYPES,
            record_filter=is_pet_visit_record,
            event_type_fn=pet_visit_event_type,
            attributes_fn=_pet_visit_attributes,
        ),
    ],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PETLIBRO event entities using config entry."""
    hub: PetLibroHub = hass.data[DOMAIN].get(entry.entry_id)

    if not hub:
        _LOGGER.error("Hub not found for entry: %s", entry.entry_id)
        return

    if not (devices := hub.devices):
        _LOGGER.warning("No devices found in hub during event setup.")
        return

    entities = [
        PetLibroEventEntity(device, hub, description)
        for device in devices.values()
        for device_type, entity_descriptions in DEVICE_EVENT_MAP.items()
        if isinstance(device, device_type)
        for description in entity_descriptions
    ]

    if entities:
        _LOGGER.debug("Adding %d PetLibro event entities", len(entities))
        async_add_entities(entities)
