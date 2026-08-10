"""User-confirmed migration helpers for UI-managed gateway sites."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import inspect
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigSubentry,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry,
    entity_registry,
    issue_registry,
)
from homeassistant.helpers.storage import Store

from ..core.const import (
    CONF_DEVICE_ROUTES,
    CONF_SITE,
    DOMAIN,
    SUBENTRY_AUX_GATEWAY,
)

_LOGGER = logging.getLogger(__name__)

TARGET_VERSION = 5
TARGET_MINOR_VERSION = 1
MINIMUM_HA_VERSION = "2026.8.1"
ISSUE_LEGACY_CONFIG_MIGRATION = "legacy_config_migration"
_NO_VIA_UPDATE = object()


class MigrationError(RuntimeError):
    """Raised when a requested gateway topology migration cannot be completed."""


class UnsupportedCoreVersionError(MigrationError):
    """Raised before mutation when Core lacks the required registry model."""


def _registry_api_supported(
    device_attributes: set[str], update_parameters: set[str]
) -> bool:
    """Return whether Core exposes the singular 2026.8 registry API."""
    return {
        "config_entry_id",
        "config_subentry_id",
    } <= device_attributes and {
        "new_config_entry_id",
        "new_config_subentry_id",
    } <= update_parameters


def is_registry_migration_supported() -> bool:
    """Return whether the running Core supports this migration implementation."""
    device_attributes = {
        attribute.name
        for attribute in device_registry.DeviceEntry.__attrs_attrs__
    }
    update_parameters = set(
        inspect.signature(
            device_registry.DeviceRegistry.async_update_device
        ).parameters
    )
    return _registry_api_supported(device_attributes, update_parameters)


def ensure_registry_migration_supported() -> None:
    """Reject unsupported Core versions before unloading or mutating entries."""
    if is_registry_migration_supported():
        return
    raise UnsupportedCoreVersionError(
        "Legacy gateway migration requires Home Assistant Core "
        f"{MINIMUM_HA_VERSION} or newer; running {HA_VERSION}. "
        "No config entries were changed by this attempt."
    )


def is_site_entry(entry: ConfigEntry) -> bool:
    """Return whether an entry already represents a UI-managed site."""
    return bool(entry.data.get(CONF_SITE))


def is_legacy_gateway_entry(entry: ConfigEntry) -> bool:
    """Return whether an entry is an upstream one-gateway config entry."""
    return (
        not is_site_entry(entry)
        and not entry.data
        and bool(entry.options.get("host"))
    )


def is_legacy_cloud_entry(entry: ConfigEntry) -> bool:
    """Return whether an entry is an upstream Mi Cloud config entry."""
    return (
        not is_site_entry(entry)
        and bool(entry.data.get("username"))
        and bool(entry.data.get("token"))
    )


def _entry_order(entry: ConfigEntry) -> tuple[object, str]:
    return (entry.created_at, entry.entry_id)


def legacy_gateway_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return legacy gateway entries in deterministic age order."""
    return sorted(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if is_legacy_gateway_entry(entry)
        ),
        key=_entry_order,
    )


def legacy_cloud_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return legacy cloud entries in deterministic age order."""
    return sorted(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if is_legacy_cloud_entry(entry)
        ),
        key=_entry_order,
    )


def gateway_entry_label(entry: ConfigEntry) -> str:
    """Return a useful label for selecting a legacy gateway entry."""
    host = str(entry.options.get("host") or "unknown host")
    model = entry.options.get("model")
    title = entry.title or host
    return f"{title} — {host}" + (f" — {model}" if model else "")


def cloud_entry_label(entry: ConfigEntry) -> str:
    """Return a useful label for selecting a legacy cloud entry."""
    username = str(entry.data.get("username") or entry.title)
    servers = ", ".join(entry.data.get("servers", [])) or "default server"
    return f"{username} — {servers}"


async def async_refresh_migration_issue(hass: HomeAssistant) -> None:
    """Expose legacy topology migration through Home Assistant Repairs."""
    gateways = legacy_gateway_entries(hass)
    if not gateways:
        issue_registry.async_delete_issue(
            hass, DOMAIN, ISSUE_LEGACY_CONFIG_MIGRATION
        )
        return

    issue_registry.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_LEGACY_CONFIG_MIGRATION,
        data={"gateway_count": len(gateways)},
        is_fixable=True,
        is_persistent=False,
        severity=issue_registry.IssueSeverity.WARNING,
        translation_key=ISSUE_LEGACY_CONFIG_MIGRATION,
        translation_placeholders={"gateway_count": str(len(gateways))},
    )


async def _restore_options_key(
    hass: HomeAssistant, source: dict[str, Any]
) -> dict[str, Any]:
    """Restore a saved gateway key without changing any legacy entry."""
    options = dict(source)
    if (key := options.get("key")) and len(key) == 16:
        return options

    token = options.get("token")
    if not token:
        return options

    store = Store(hass, 1, f"{DOMAIN}/keys.json")
    for device in (await store.async_load() or {}).values():
        if device.get("token") == token and device.get("key"):
            options["key"] = device["key"]
            break
    return options


def _account_unique_id(entry: ConfigEntry) -> str:
    username = str(entry.data.get("username", entry.entry_id))
    servers = ",".join(sorted(entry.data.get("servers", [])))
    return f"account:{username}:{servers}"


def _gateway_unique_id(options: dict[str, Any]) -> str:
    return str(options.get("did") or options.get("host"))


def _gateway_root_ids(devices: Iterable[Any]) -> set[str]:
    """Identify gateway root devices without changing child topology."""
    devices = list(devices)
    roots = {
        device.id
        for device in devices
        if str(device.model or "").startswith("Gateway")
    }
    if roots:
        return roots

    network_roots = {
        device.id
        for device in devices
        if any(
            connection[0] == device_registry.CONNECTION_NETWORK_MAC
            for connection in device.connections
        )
    }
    if network_roots:
        return network_roots

    referenced_parents = {
        device.via_device_id
        for device in devices
        if device.via_device_id is not None
    }
    roots = {device.id for device in devices if device.id in referenced_parents}
    if roots:
        return roots

    return {
        device.id for device in devices if device.via_device_id is None
    }


def _devices_for_entry(registry: Any, entry_id: str) -> list[Any]:
    return [
        device
        for device in list(registry.devices.values())
        if device.config_entry_id == entry_id
    ]


def _entities_for_entry(registry: Any, entry_id: str) -> list[Any]:
    return [
        entity
        for entity in list(registry.entities.values())
        if entity.config_entry_id == entry_id
    ]


def _matching_target_device(
    registry: Any, source: Any, target_entry_id: str
) -> Any | None:
    """Find one same-identity device already owned by the target entry."""
    matches: dict[str, Any] = {}
    for identifier in source.identifiers:
        if match := registry.async_get_device_by_identifier(
            identifier, target_entry_id
        ):
            matches[match.id] = match
    for connection in source.connections:
        if match := registry.async_get_device_by_connection(
            connection, target_entry_id
        ):
            matches[match.id] = match

    if len(matches) > 1:
        raise MigrationError(
            f"Device {source.id} matches multiple devices in the target entry"
        )
    return next(iter(matches.values()), None)


def _device_update_kwargs(
    source: Any,
    target: Any,
    target_subentry_id: str | None,
    target_via_id: str | None | object,
) -> dict[str, Any]:
    """Preserve user metadata while assigning canonical ownership."""
    kwargs: dict[str, Any] = {}
    if target.config_subentry_id != target_subentry_id:
        kwargs["new_config_subentry_id"] = target_subentry_id
    if (
        target_via_id is not _NO_VIA_UPDATE
        and target.via_device_id != target_via_id
    ):
        kwargs["via_device_id"] = target_via_id

    labels = set(target.labels) | set(source.labels)
    if labels != set(target.labels):
        kwargs["labels"] = labels
    if target.area_id is None and source.area_id is not None:
        kwargs["area_id"] = source.area_id
    if target.name_by_user is None and source.name_by_user is not None:
        kwargs["name_by_user"] = source.name_by_user
    if (
        target.disabled_by is None
        and source.disabled_by is device_registry.DeviceEntryDisabler.USER
    ):
        kwargs["disabled_by"] = device_registry.DeviceEntryDisabler.USER
    return kwargs


def _move_entity(
    registry: Any,
    entity: Any,
    target_entry_id: str,
    target_subentry_id: str | None,
    target_device_id: str | None,
) -> None:
    registry.async_update_entity(
        entity.entity_id,
        config_entry_id=target_entry_id,
        config_subentry_id=target_subentry_id,
        device_id=target_device_id,
    )


def _move_entry_registry(
    hass: HomeAssistant,
    source_entry_id: str,
    target_entry_id: str,
    *,
    gateway_subentry_id: str | None,
    child_anchor_id: str | None = None,
) -> set[str]:
    """Move one legacy entry into a site using the Core 2026.8 registry model."""
    dev_reg = device_registry.async_get(hass)
    ent_reg = entity_registry.async_get(hass)
    source_devices = _devices_for_entry(dev_reg, source_entry_id)
    source_entities = _entities_for_entry(ent_reg, source_entry_id)
    root_ids = _gateway_root_ids(source_devices)
    entities_by_device: dict[str, list[Any]] = {}
    entry_entities: list[Any] = []
    for entity in source_entities:
        if entity.device_id is None:
            entry_entities.append(entity)
        else:
            entities_by_device.setdefault(entity.device_id, []).append(entity)

    device_map: dict[str, str] = {}
    target_root_ids: set[str] = set()
    ordered_devices = sorted(
        source_devices,
        key=lambda device: (device.id not in root_ids, device.id),
    )

    for source in ordered_devices:
        is_root = source.id in root_ids
        target_subentry_id = gateway_subentry_id if is_root else None
        if is_root:
            target_via_id: str | None | object = None
        elif child_anchor_id is not None:
            target_via_id = child_anchor_id
        elif source.via_device_id in device_map:
            target_via_id = device_map[source.via_device_id]
        else:
            target_via_id = _NO_VIA_UPDATE

        target = _matching_target_device(dev_reg, source, target_entry_id)
        if target is None:
            update: dict[str, Any] = {
                "new_config_entry_id": target_entry_id,
                "new_config_subentry_id": target_subentry_id,
            }
            if target_via_id is not _NO_VIA_UPDATE:
                update["via_device_id"] = target_via_id
            target = dev_reg.async_update_device(source.id, **update)
            if target is None:
                raise MigrationError(
                    f"Device {source.id} was removed instead of moved"
                )
        else:
            update = _device_update_kwargs(
                source, target, target_subentry_id, target_via_id
            )
            if update:
                target = dev_reg.async_update_device(target.id, **update)
                if target is None:
                    raise MigrationError(
                        f"Canonical device {source.id} was unexpectedly removed"
                    )

        device_map[source.id] = target.id
        if is_root:
            target_root_ids.add(target.id)

        for entity in entities_by_device.pop(source.id, []):
            _move_entity(
                ent_reg,
                entity,
                target_entry_id,
                target_subentry_id,
                target.id,
            )

        if target.id != source.id and dev_reg.async_get(source.id) is not None:
            dev_reg.async_remove_device(source.id)

    for entities in entities_by_device.values():
        for entity in entities:
            target_device_id = device_map.get(entity.device_id)
            _move_entity(
                ent_reg,
                entity,
                target_entry_id,
                None,
                target_device_id,
            )

    for entity in entry_entities:
        _move_entity(ent_reg, entity, target_entry_id, None, None)

    return target_root_ids


async def _unload_entries(
    hass: HomeAssistant, entries: Iterable[ConfigEntry]
) -> set[str]:
    """Unload entries and return the IDs that were loaded beforehand."""
    loaded_ids: set[str] = set()
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            loaded_ids.add(entry.entry_id)
            if not await hass.config_entries.async_unload(entry.entry_id):
                raise MigrationError(f"Could not unload {entry.title}")
    return loaded_ids


async def _restore_loaded_entries(
    hass: HomeAssistant, loaded_ids: Iterable[str]
) -> None:
    for entry_id in loaded_ids:
        if hass.config_entries.async_get_entry(entry_id) is None:
            continue
        try:
            await hass.config_entries.async_setup(entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not restore config entry %s", entry_id)


async def _async_refresh_migration_issue_after_fix(
    hass: HomeAssistant,
) -> None:
    """Refresh after RepairsFlowManager has deleted the completed issue."""
    await asyncio.sleep(0)
    await async_refresh_migration_issue(hass)


async def async_migrate_legacy_site(
    hass: HomeAssistant,
    *,
    cloud_entry_id: str | None,
    main_entry_id: str,
    aux_entry_ids: Iterable[str],
) -> ConfigEntry:
    """Apply a topology selected and confirmed through a Repairs flow."""
    ensure_registry_migration_supported()
    gateways = {entry.entry_id: entry for entry in legacy_gateway_entries(hass)}
    clouds = {entry.entry_id: entry for entry in legacy_cloud_entries(hass)}

    if (main := gateways.get(main_entry_id)) is None:
        raise MigrationError("The selected main gateway no longer exists")

    aux_ids = list(dict.fromkeys(aux_entry_ids))
    if main_entry_id in aux_ids:
        raise MigrationError("The main gateway cannot also be auxiliary")
    try:
        auxiliaries = [gateways[entry_id] for entry_id in aux_ids]
    except KeyError as err:
        raise MigrationError(
            "A selected auxiliary gateway no longer exists"
        ) from err

    cloud = None
    if cloud_entry_id is not None:
        cloud = clouds.get(cloud_entry_id)
        if cloud is None:
            raise MigrationError("The selected Mi Cloud entry no longer exists")

    parent = cloud or main
    entries: list[ConfigEntry] = []
    for entry in (parent, main, *auxiliaries):
        if entry not in entries:
            entries.append(entry)

    main_options = await _restore_options_key(hass, dict(main.options))
    if not main_options.get("host") or not main_options.get("token"):
        raise MigrationError("The selected main gateway has no usable credentials")

    aux_subentries: list[tuple[ConfigEntry, ConfigSubentry]] = []
    for auxiliary in auxiliaries:
        options = await _restore_options_key(hass, dict(auxiliary.options))
        if not options.get("host") or not options.get("token"):
            raise MigrationError(
                f"Auxiliary gateway {auxiliary.title} has no usable credentials"
            )
        aux_subentries.append(
            (
                auxiliary,
                ConfigSubentry(
                    data=MappingProxyType(options),
                    subentry_type=SUBENTRY_AUX_GATEWAY,
                    title=auxiliary.title or str(options["host"]),
                    unique_id=auxiliary.unique_id or _gateway_unique_id(options),
                ),
            )
        )

    loaded_ids = await _unload_entries(hass, entries)
    try:
        for _, subentry in aux_subentries:
            hass.config_entries.async_add_subentry(parent, subentry)

        data = dict(cloud.data) if cloud else {}
        data[CONF_SITE] = True
        unique_id = (
            parent.unique_id
            if parent.unique_id is not None
            else _account_unique_id(parent)
            if cloud
            else main.unique_id
        )
        hass.config_entries.async_update_entry(
            parent,
            data=data,
            options=main_options,
            unique_id=unique_id,
            version=TARGET_VERSION,
            minor_version=TARGET_MINOR_VERSION,
        )

        if main.entry_id == parent.entry_id:
            main_roots = _gateway_root_ids(
                _devices_for_entry(
                    device_registry.async_get(hass), parent.entry_id
                )
            )
        else:
            main_roots = _move_entry_registry(
                hass,
                main.entry_id,
                parent.entry_id,
                gateway_subentry_id=None,
            )
        child_anchor_id = min(main_roots) if main_roots else None

        for auxiliary, subentry in aux_subentries:
            _move_entry_registry(
                hass,
                auxiliary.entry_id,
                parent.entry_id,
                gateway_subentry_id=subentry.subentry_id,
                child_anchor_id=child_anchor_id,
            )

        if not await hass.config_entries.async_setup(parent.entry_id):
            raise MigrationError("The migrated gateway site could not be set up")

        # Keep every source entry until the new site has proved it can load.
        for source in (main, *auxiliaries):
            if source.entry_id != parent.entry_id:
                await hass.config_entries.async_remove(source.entry_id)
    except Exception as err:
        await _restore_loaded_entries(hass, loaded_ids)
        if isinstance(err, MigrationError):
            raise
        raise MigrationError("Unexpected failure while migrating gateway entries") from err

    hass.async_create_task(
        _async_refresh_migration_issue_after_fix(hass),
        "refresh XiaomiGateway3 legacy migration repair",
    )
    return parent


def _promoted_main_options(
    current_options: dict[str, Any],
    promoted_data: dict[str, Any],
    promoted_subentry_id: str,
) -> dict[str, Any]:
    """Build options after an auxiliary gateway becomes main."""
    routes = {
        uid: route
        for uid, route in dict(
            current_options.get(CONF_DEVICE_ROUTES, {})
        ).items()
        if route != promoted_subentry_id
    }
    options = dict(promoted_data)
    if routes:
        options[CONF_DEVICE_ROUTES] = routes
    return options


def _move_subentry_entities(
    registry: Any,
    entry_id: str,
    source_subentry_id: str | None,
    target_subentry_id: str | None,
    device_ids: set[str],
) -> None:
    for entity in list(registry.entities.values()):
        if entity.config_entry_id != entry_id:
            continue
        if entity.device_id in device_ids or (
            entity.config_subentry_id == source_subentry_id
            and source_subentry_id is not None
        ):
            registry.async_update_entity(
                entity.entity_id,
                config_subentry_id=target_subentry_id,
            )


async def async_promote_aux_gateway(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry_id: str,
) -> None:
    """Promote an explicitly selected auxiliary gateway to main."""
    ensure_registry_migration_supported()
    if not is_site_entry(entry):
        raise MigrationError("This config entry is not a gateway site")
    selected = entry.subentries.get(subentry_id)
    if selected is None or selected.subentry_type != SUBENTRY_AUX_GATEWAY:
        raise MigrationError("The selected auxiliary gateway no longer exists")

    old_main = dict(entry.options)
    old_main.pop(CONF_DEVICE_ROUTES, None)
    if not old_main.get("host") or not old_main.get("token"):
        raise MigrationError("The current main gateway has no usable credentials")

    old_main_subentry = ConfigSubentry(
        data=MappingProxyType(old_main),
        subentry_type=SUBENTRY_AUX_GATEWAY,
        title=str(old_main.get("host") or "Former main gateway"),
        unique_id=_gateway_unique_id(old_main),
    )
    new_options = _promoted_main_options(
        dict(entry.options), dict(selected.data), subentry_id
    )

    loaded_ids = await _unload_entries(hass, [entry])
    try:
        hass.config_entries.async_add_subentry(entry, old_main_subentry)

        dev_reg = device_registry.async_get(hass)
        ent_reg = entity_registry.async_get(hass)
        devices = _devices_for_entry(dev_reg, entry.entry_id)
        main_candidates = [
            device for device in devices if device.config_subentry_id is None
        ]
        old_main_ids = _gateway_root_ids(main_candidates)
        promoted_ids = {
            device.id
            for device in devices
            if device.config_subentry_id == subentry_id
        }

        for device_id in promoted_ids:
            dev_reg.async_update_device(
                device_id, new_config_subentry_id=None
            )
        _move_subentry_entities(
            ent_reg,
            entry.entry_id,
            subentry_id,
            None,
            promoted_ids,
        )

        for device_id in old_main_ids:
            dev_reg.async_update_device(
                device_id,
                new_config_subentry_id=old_main_subentry.subentry_id,
            )
        _move_subentry_entities(
            ent_reg,
            entry.entry_id,
            None,
            old_main_subentry.subentry_id,
            old_main_ids,
        )

        hass.config_entries.async_update_entry(entry, options=new_options)
        hass.config_entries.async_remove_subentry(entry, subentry_id)

        if not await hass.config_entries.async_setup(entry.entry_id):
            raise MigrationError("The gateway site could not be set up after promotion")
    except Exception as err:
        await _restore_loaded_entries(hass, loaded_ids)
        if isinstance(err, MigrationError):
            raise
        raise MigrationError("Unexpected failure while changing the main gateway") from err
