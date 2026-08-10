import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .core import logger
from .core.const import CONF_DEVICE_ROUTES, CONF_SITE, DOMAIN
from .core.device import XDevice
from .core.gateway import MultiGateway
from .hass import hass_utils
from .hass.add_entitites import handle_add_entities
from .hass.entity import XEntity
from .hass.migration import (
    TARGET_MINOR_VERSION,
    TARGET_VERSION,
    async_refresh_migration_issue,
)
from .hass.site import GatewaySite, get_aux_subentries

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "light",
    "number",
    "select",
    "sensor",
    "switch",
    "text",
]

CONF_DEVICES = "devices"
CONF_ATTRIBUTES_TEMPLATE = "attributes_template"
CONF_OPENMIIO = "openmiio"
CONF_LOGGER = "logger"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                CONF_LOGGER: logger.CONFIG_SCHEMA,
                vol.Optional(CONF_ATTRIBUTES_TEMPLATE): cv.template,
            },
            extra=vol.ALLOW_EXTRA,
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if config := config.get(DOMAIN):
        if devices_config := config.get(CONF_DEVICES):
            # Keep upstream YAML device customization exactly as-is. The
            # multi-gateway feature never reads or writes this mapping.
            XDevice.configs = hass_utils.fix_yaml_devices_config(devices_config)

        if logger_config := config.get(CONF_LOGGER):
            _ = hass.async_add_executor_job(
                logger.init, __name__, logger_config, hass.config.config_dir
            )

        if template := config.get(CONF_ATTRIBUTES_TEMPLATE):
            template.hass = hass
            XEntity.attributes_template = template

    hass.data[DOMAIN] = {}
    await hass_utils.store_devices(hass)
    # Legacy entries continue to run unchanged. Home Assistant Repairs offers
    # the topology migration and performs it only after explicit confirmation.
    await async_refresh_migration_issue(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.data.get(CONF_SITE):
        return await _async_setup_site(hass, entry)

    # Untouched upstream compatibility paths remain active until a Repairs
    # migration is explicitly completed by the user.
    if entry.data:
        return await hass_utils.setup_cloud(hass, entry)

    return await _async_setup_legacy_gateway(hass, entry)


async def _async_setup_site(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass_utils.store_gateway_key(hass, entry)
    await _async_prepare_site_entry(hass, entry)

    cloud = None
    if entry.data.get("username"):
        await hass_utils.setup_cloud(hass, entry)
        cloud = hass.data[DOMAIN].pop(entry.entry_id, None)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    site = GatewaySite(hass, entry, cloud)
    for gateway in site.gateways:
        handle_add_entities(
            hass,
            entry,
            gateway,
            config_subentry_id=getattr(
                gateway, "config_subentry_id", None
            ),
        )
    site.start()
    hass.data[DOMAIN][entry.entry_id] = site
    _register_entry_lifecycle(hass, entry, site.stop)
    return True


async def _async_prepare_site_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    subentries = get_aux_subentries(entry)
    valid_aux_ids = {subentry.subentry_id for subentry in subentries}

    options = dict(entry.options)
    current_routes = dict(options.get(CONF_DEVICE_ROUTES, {}))
    routes = {
        uid: subentry_id
        for uid, subentry_id in current_routes.items()
        if subentry_id in valid_aux_ids
    }
    if routes != current_routes:
        if routes:
            options[CONF_DEVICE_ROUTES] = routes
        else:
            options.pop(CONF_DEVICE_ROUTES, None)
        hass.config_entries.async_update_entry(entry, options=options)

    for subentry in subentries:
        data = dict(subentry.data)
        if (key := data.get("key")) and len(key) == 16:
            continue
        if token := data.get("token"):
            if key := await hass_utils.restore_gateway_key(hass, token):
                data["key"] = key
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=data
                )


async def _async_setup_legacy_gateway(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    await hass_utils.store_gateway_key(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    gateway = MultiGateway(**entry.options)
    handle_add_entities(hass, entry, gateway)
    gateway.start()
    hass.data[DOMAIN][entry.entry_id] = gateway
    _register_entry_lifecycle(hass, entry, gateway.stop)
    return True


def _register_entry_lifecycle(hass, entry, stop_handler) -> None:
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def hass_stop(event):
        await stop_handler()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hass_stop)
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.data and not entry.data.get(CONF_SITE):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True

    hass_utils.remove_stats_entities(hass, entry)
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    runtime = hass.data[DOMAIN].pop(entry.entry_id, None)
    if isinstance(runtime, GatewaySite):
        await runtime.stop()
    elif isinstance(runtime, MultiGateway):
        await runtime.stop()
        runtime.remove_all_devices()

    return ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate the entry schema without changing the user's topology."""
    if config_entry.version == 1:
        hass_utils.migrate_legacy_devices_unique_id(hass)
        hass_utils.migrate_legacy_entitites_unique_id(hass)
        hass_utils.migrate_devices_store()

    hass.config_entries.async_update_entry(
        config_entry,
        version=TARGET_VERSION,
        minor_version=TARGET_MINOR_VERSION,
    )
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Supported from Hass v2022.3"""
    device_registry.async_get(hass).async_remove_device(device_entry.id)
    return True
