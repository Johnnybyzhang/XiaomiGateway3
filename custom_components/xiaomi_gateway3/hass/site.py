"""UI-managed collection of one main and multiple auxiliary gateways."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry

from ..core.const import (
    CONF_DEVICE_ROUTES,
    DOMAIN,
    GATEWAY,
    ROUTE_MAIN,
    SUBENTRY_AUX_GATEWAY,
)
from ..core.device import XDevice
from ..core.gate.base import (
    EVENT_ADD_DEVICE,
    EVENT_REMOVE_DEVICE,
    XGateway,
)
from ..core.gateway import MultiGateway
from ..core.xiaomi_cloud import MiCloud

_LOGGER = logging.getLogger(__name__)


def get_aux_subentries(entry: ConfigEntry) -> tuple[ConfigSubentry, ...]:
    """Return auxiliary gateway subentries using the stable mapping API."""
    return tuple(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_AUX_GATEWAY
    )


class GatewaySite:
    """One Home Assistant entry representing one physical space/account."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        cloud: MiCloud | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.cloud = cloud
        self.routes: dict[str, str] = dict(
            entry.options.get(CONF_DEVICE_ROUTES, {})
        )

        main_options = dict(entry.options)
        main_options.pop(CONF_DEVICE_ROUTES, None)
        self.main = MultiGateway(**main_options)
        self.main.config_subentry_id = None
        self.main.site = self

        self.aux: dict[str, MultiGateway] = {}
        self._titles: dict[str, str] = {}

        self._register_gateway(self.main)
        for subentry in get_aux_subentries(entry):
            gateway = MultiGateway(**dict(subentry.data))
            gateway.config_subentry_id = subentry.subentry_id
            gateway.site = self
            self.aux[subentry.subentry_id] = gateway
            self._titles[subentry.subentry_id] = subentry.title
            self._register_gateway(gateway)

    @property
    def gateways(self) -> tuple[MultiGateway, ...]:
        """Return main first, followed by auxiliary gateways."""
        return (self.main, *self.aux.values())

    def _register_gateway(self, gateway: MultiGateway) -> None:
        gateway.add_event_listener(
            EVENT_ADD_DEVICE,
            lambda device, gateway=gateway: self._device_added(
                gateway, device
            ),
        )
        gateway.add_event_listener(
            EVENT_REMOVE_DEVICE,
            lambda device, gateway=gateway: self._device_removed(
                gateway, device
            ),
        )

    def _device_added(self, gateway: XGateway, device: XDevice) -> None:
        if device.type == GATEWAY:
            return

        device.default_gateway = self.main
        device.preferred_gateway = self.aux.get(self.routes.get(device.uid))

    @staticmethod
    def _device_removed(gateway: XGateway, device: XDevice) -> None:
        if device.preferred_gateway is gateway:
            device.preferred_gateway = None
        if device.default_gateway is gateway:
            device.default_gateway = None

    def start(self) -> None:
        """Start main first, then all auxiliary gateways."""
        for gateway in self.gateways:
            gateway.start()

    async def stop(self) -> None:
        """Stop gateways and remove devices exclusive to deleted auxiliaries."""
        current_entry = self.hass.config_entries.async_get_entry(
            self.entry.entry_id
        )
        current_aux_ids = (
            {
                subentry.subentry_id
                for subentry in get_aux_subentries(current_entry)
            }
            if current_entry is not None
            else set()
        )
        removed_aux_ids = set(self.aux) - current_aux_ids
        removed_gateways = {
            self.aux[subentry_id] for subentry_id in removed_aux_ids
        }
        orphan_uids = self._orphan_uids(removed_gateways)

        await asyncio.gather(*(gateway.stop() for gateway in self.gateways))
        for gateway in self.gateways:
            gateway.remove_all_devices()

        if orphan_uids:
            self._remove_orphan_registry_entries(orphan_uids)

    def _orphan_uids(self, removed_gateways: set[XGateway]) -> set[str]:
        if not removed_gateways:
            return set()

        orphan_uids = set()
        for device in self.devices:
            providers = set(device.gateways)
            if providers and providers.issubset(removed_gateways):
                orphan_uids.add(device.uid)
        return orphan_uids

    def _remove_orphan_registry_entries(self, uids: set[str]) -> None:
        """Remove only parent-owned devices with no remaining gateway provider."""
        dev_reg = device_registry.async_get(self.hass)
        ent_reg = entity_registry.async_get(self.hass)

        for uid in uids:
            device_entry = dev_reg.async_get_device_by_identifier(
                (DOMAIN, uid), self.entry.entry_id
            )
            if device_entry is None:
                continue

            for entity in list(ent_reg.entities.values()):
                if (
                    entity.config_entry_id == self.entry.entry_id
                    and entity.device_id == device_entry.id
                ):
                    ent_reg.async_remove(entity.entity_id)

            dev_reg.async_remove_device(device_entry.id)
            _LOGGER.debug("Removed orphaned gateway device %s", uid)

    @property
    def devices(self) -> list[XDevice]:
        """Return devices currently provided by at least one site gateway."""
        gateways = set(self.gateways)
        devices = {
            device.uid: device
            for gateway in self.gateways
            for device in gateway.devices.values()
            if device.type != GATEWAY
            and any(provider in gateways for provider in device.gateways)
        }
        return sorted(
            devices.values(), key=lambda item: (item.human_name, item.uid)
        )

    def gateway_options(self, device: XDevice) -> dict[str, str]:
        """Return valid UI route choices for a device."""
        options = {ROUTE_MAIN: self.gateway_label(self.main)}
        selected = self.routes.get(device.uid)

        for subentry_id, gateway in self.aux.items():
            if gateway in device.gateways or subentry_id == selected:
                options[subentry_id] = self.gateway_label(gateway)

        return options

    def gateway_label(self, gateway: MultiGateway) -> str:
        """Return a stable human-readable gateway label."""
        subentry_id = getattr(gateway, "config_subentry_id", None)
        if gateway.device:
            name = gateway.device.human_name
        elif subentry_id:
            name = self._titles.get(subentry_id, "Aux gateway")
        else:
            name = "Main gateway"
        return f"{name} ({gateway.host})"
