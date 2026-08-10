from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file.write_text(text.replace(old, new, 1))


path = "custom_components/xiaomi_gateway3/hass/migration.py"

replace_once(
    path,
    '''from ..core.const import (
    CONF_DEVICE_ROUTES,
    CONF_SITE,
    DOMAIN,
    SUBENTRY_AUX_GATEWAY,
)
''',
    '''from ..core.const import (
    CONF_DEVICE_ROUTES,
    CONF_SITE,
    DOMAIN,
    SUBENTRY_AUX_GATEWAY,
)
from .site import (
    ensure_main_subentry,
    get_main_subentry,
    main_gateway_title,
    strip_main_gateway_title,
)
''',
)
replace_once(
    path,
    '''TARGET_VERSION = 5
TARGET_MINOR_VERSION = 1
''',
    '''TARGET_VERSION = 5
TARGET_MINOR_VERSION = 2
''',
)

replace_once(
    path,
    '''def _move_entry_registry(
    hass: HomeAssistant,
''',
    '''def assign_unscoped_devices_to_main(
    hass: HomeAssistant,
    entry_id: str,
    main_subentry_id: str,
) -> None:
    """Move parent-owned devices/entities under the managed main subentry."""
    dev_reg = device_registry.async_get(hass)
    ent_reg = entity_registry.async_get(hass)
    devices = [
        device
        for device in _devices_for_entry(dev_reg, entry_id)
        if device.config_subentry_id is None
    ]
    device_ids = {device.id for device in devices}

    _move_subentry_entities(
        ent_reg,
        entry_id,
        None,
        main_subentry_id,
        device_ids,
        include_entry_entities=True,
    )
    for device in devices:
        if dev_reg.async_get(device.id) is not None:
            dev_reg.async_update_device(
                device.id,
                new_config_subentry_id=main_subentry_id,
            )


def _move_entry_registry(
    hass: HomeAssistant,
''',
)
replace_once(
    path,
    '''    *,
    gateway_subentry_id: str | None,
    child_anchor_id: str | None = None,
) -> set[str]:
''',
    '''    *,
    gateway_subentry_id: str | None,
    child_subentry_id: str | None = None,
    child_anchor_id: str | None = None,
) -> set[str]:
''',
)
replace_once(
    path,
    '''        target_subentry_id = gateway_subentry_id if is_root else None
''',
    '''        target_subentry_id = (
            gateway_subentry_id if is_root else child_subentry_id
        )
''',
)

replace_once(
    path,
    '''        hass.config_entries.async_update_entry(
            parent,
            data=data,
            options=main_options,
            unique_id=unique_id,
            version=TARGET_VERSION,
            minor_version=TARGET_MINOR_VERSION,
        )

        if main.entry_id == parent.entry_id:
''',
    '''        hass.config_entries.async_update_entry(
            parent,
            data=data,
            options=main_options,
            unique_id=unique_id,
            version=TARGET_VERSION,
            minor_version=TARGET_MINOR_VERSION,
        )
        main_subentry = ensure_main_subentry(
            hass, parent, title_hint=main.title
        )

        if main.entry_id == parent.entry_id:
''',
)
replace_once(
    path,
    '''        if main.entry_id == parent.entry_id:
            main_roots = _gateway_root_ids(
                _devices_for_entry(
                    device_registry.async_get(hass), parent.entry_id
                )
            )
        else:
''',
    '''        if main.entry_id == parent.entry_id:
            assign_unscoped_devices_to_main(
                hass, parent.entry_id, main_subentry.subentry_id
            )
            main_roots = _gateway_root_ids(
                device
                for device in _devices_for_entry(
                    device_registry.async_get(hass), parent.entry_id
                )
                if device.config_subentry_id == main_subentry.subentry_id
            )
        else:
''',
)
replace_once(
    path,
    '''                main.entry_id,
                parent.entry_id,
                gateway_subentry_id=None,
            )
''',
    '''                main.entry_id,
                parent.entry_id,
                gateway_subentry_id=main_subentry.subentry_id,
                child_subentry_id=main_subentry.subentry_id,
            )
''',
)
replace_once(
    path,
    '''                auxiliary.entry_id,
                parent.entry_id,
                gateway_subentry_id=subentry.subentry_id,
                child_anchor_id=child_anchor_id,
            )
''',
    '''                auxiliary.entry_id,
                parent.entry_id,
                gateway_subentry_id=subentry.subentry_id,
                child_subentry_id=main_subentry.subentry_id,
                child_anchor_id=child_anchor_id,
            )
''',
)

replace_once(
    path,
    '''    loaded_ids = await _unload_entries(hass, entries)
    try:
        if main_source is not None:
''',
    '''    loaded_ids = await _unload_entries(hass, entries)
    try:
        main_subentry = ensure_main_subentry(
            hass, parent, title_hint=candidate.main.title
        )
        if main_source is not None:
''',
)
replace_once(
    path,
    '''                main_source.entry_id,
                parent.entry_id,
                gateway_subentry_id=None,
            )

        parent_devices = _devices_for_entry(
''',
    '''                main_source.entry_id,
                parent.entry_id,
                gateway_subentry_id=main_subentry.subentry_id,
                child_subentry_id=main_subentry.subentry_id,
            )
        assign_unscoped_devices_to_main(
            hass, parent.entry_id, main_subentry.subentry_id
        )

        parent_devices = _devices_for_entry(
''',
)
replace_once(
    path,
    '''            device
            for device in parent_devices
            if device.config_subentry_id is None
        )
''',
    '''            device
            for device in parent_devices
            if device.config_subentry_id == main_subentry.subentry_id
        )
''',
)
replace_once(
    path,
    '''                auxiliary.entry_id,
                parent.entry_id,
                gateway_subentry_id=subentry.subentry_id,
                child_anchor_id=child_anchor_id,
            )

        if not await hass.config_entries.async_setup(parent.entry_id):
''',
    '''                auxiliary.entry_id,
                parent.entry_id,
                gateway_subentry_id=subentry.subentry_id,
                child_subentry_id=main_subentry.subentry_id,
                child_anchor_id=child_anchor_id,
            )

        if not await hass.config_entries.async_setup(parent.entry_id):
''',
)

replace_once(
    path,
    '''def _move_subentry_entities(
    registry: Any,
    entry_id: str,
    source_subentry_id: str | None,
    target_subentry_id: str | None,
    device_ids: set[str],
) -> None:
''',
    '''def _move_subentry_entities(
    registry: Any,
    entry_id: str,
    source_subentry_id: str | None,
    target_subentry_id: str | None,
    device_ids: set[str],
    *,
    include_entry_entities: bool = False,
) -> None:
''',
)
replace_once(
    path,
    '''        if current.device_id in device_ids or (
            current.config_subentry_id == source_subentry_id
            and source_subentry_id is not None
        ):
''',
    '''        if current.device_id in device_ids or (
            include_entry_entities
            and current.device_id is None
            and current.config_subentry_id == source_subentry_id
        ) or (
            current.config_subentry_id == source_subentry_id
            and source_subentry_id is not None
        ):
''',
)

replace_once(
    path,
    '''    selected = entry.subentries.get(subentry_id)
    if selected is None or selected.subentry_type != SUBENTRY_AUX_GATEWAY:
        raise MigrationError("The selected auxiliary gateway no longer exists")

    old_main = dict(entry.options)
''',
    '''    selected = entry.subentries.get(subentry_id)
    if selected is None or selected.subentry_type != SUBENTRY_AUX_GATEWAY:
        raise MigrationError("The selected auxiliary gateway no longer exists")
    main_subentry = get_main_subentry(entry)
    if main_subentry is None:
        raise MigrationError("The gateway site has no main gateway subentry")

    old_main = dict(entry.options)
''',
)
replace_once(
    path,
    '''    old_main_subentry = ConfigSubentry(
        data=MappingProxyType(old_main),
        subentry_type=SUBENTRY_AUX_GATEWAY,
        title=str(old_main.get("host") or "Former main gateway"),
        unique_id=_gateway_unique_id(old_main),
    )
''',
    '''    old_main_unique_id = _gateway_unique_id(old_main)
    old_main_subentry = ConfigSubentry(
        data=MappingProxyType(old_main),
        subentry_type=SUBENTRY_AUX_GATEWAY,
        title=strip_main_gateway_title(main_subentry.title),
        # Main still owns this identity until the promotion commits.
        unique_id=None,
    )
''',
)
replace_once(
    path,
    '''        main_candidates = [
            device for device in devices if device.config_subentry_id is None
        ]
''',
    '''        main_candidates = [
            device
            for device in devices
            if device.config_subentry_id == main_subentry.subentry_id
        ]
''',
)
replace_once(
    path,
    '''            ent_reg,
            entry.entry_id,
            subentry_id,
            None,
            promoted_ids,
        )
''',
    '''            ent_reg,
            entry.entry_id,
            subentry_id,
            main_subentry.subentry_id,
            promoted_ids,
        )
''',
)
replace_once(
    path,
    '''                    device_id, new_config_subentry_id=None
''',
    '''                    device_id,
                    new_config_subentry_id=main_subentry.subentry_id,
''',
)
replace_once(
    path,
    '''            ent_reg,
            entry.entry_id,
            None,
            old_main_subentry.subentry_id,
            old_main_ids,
''',
    '''            ent_reg,
            entry.entry_id,
            main_subentry.subentry_id,
            old_main_subentry.subentry_id,
            old_main_ids,
''',
)
replace_once(
    path,
    '''        hass.config_entries.async_update_entry(entry, options=new_options)
        hass.config_entries.async_remove_subentry(entry, subentry_id)
''',
    '''        hass.config_entries.async_update_entry(entry, options=new_options)
        selected_unique_id = selected.unique_id
        hass.config_entries.async_remove_subentry(entry, subentry_id)
        hass.config_entries.async_update_subentry(
            entry,
            main_subentry,
            data=dict(selected.data),
            title=main_gateway_title(selected.title),
            unique_id=selected_unique_id,
        )
        hass.config_entries.async_update_subentry(
            entry,
            old_main_subentry,
            unique_id=old_main_unique_id,
        )
''',
)
