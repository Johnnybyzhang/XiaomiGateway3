from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file.write_text(text.replace(old, new, 1))


replace_once(
    "custom_components/xiaomi_gateway3/core/const.py",
    '''CONF_DEVICE_ROUTES = "device_routes"
SUBENTRY_AUX_GATEWAY = "aux_gateway"
ROUTE_MAIN = "main"
''',
    '''CONF_DEVICE_ROUTES = "device_routes"
SUBENTRY_MAIN_GATEWAY = "main_gateway"
SUBENTRY_AUX_GATEWAY = "aux_gateway"
ROUTE_MAIN = "main"
''',
)

replace_once(
    "custom_components/xiaomi_gateway3/hass/site.py",
    '''from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
''',
    '''from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/site.py",
    '''    ROUTE_MAIN,
    SUBENTRY_AUX_GATEWAY,
)
''',
    '''    ROUTE_MAIN,
    SUBENTRY_AUX_GATEWAY,
    SUBENTRY_MAIN_GATEWAY,
)
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/site.py",
    '''_LOGGER = logging.getLogger(__name__)


def get_aux_subentries(entry: ConfigEntry) -> tuple[ConfigSubentry, ...]:
''',
    '''_LOGGER = logging.getLogger(__name__)

MAIN_GATEWAY_TITLE_PREFIX = "0 Main · "


def main_gateway_title(name: object) -> str:
    """Return a deterministic title that keeps main first in the HA UI."""
    value = str(name or "Gateway").strip()
    if value.startswith(MAIN_GATEWAY_TITLE_PREFIX):
        return value
    return f"{MAIN_GATEWAY_TITLE_PREFIX}{value}"


def strip_main_gateway_title(title: str) -> str:
    """Return the gateway name without the managed sort prefix."""
    if title.startswith(MAIN_GATEWAY_TITLE_PREFIX):
        return title.removeprefix(MAIN_GATEWAY_TITLE_PREFIX)
    return title


def get_main_subentry(entry: ConfigEntry) -> ConfigSubentry | None:
    """Return the single managed main-gateway subentry, if present."""
    return next(
        (
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_MAIN_GATEWAY
        ),
        None,
    )


def get_aux_subentries(entry: ConfigEntry) -> tuple[ConfigSubentry, ...]:
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/site.py",
    '''def get_aux_subentries(entry: ConfigEntry) -> tuple[ConfigSubentry, ...]:
    """Return auxiliary gateway subentries using the stable mapping API."""
    return tuple(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_AUX_GATEWAY
    )


class GatewaySite:
''',
    '''def get_aux_subentries(entry: ConfigEntry) -> tuple[ConfigSubentry, ...]:
    """Return auxiliary gateway subentries using the stable mapping API."""
    return tuple(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_AUX_GATEWAY
    )


def _main_gateway_data(entry: ConfigEntry) -> dict:
    data = dict(entry.options)
    data.pop(CONF_DEVICE_ROUTES, None)
    return data


def _main_gateway_name(
    hass: HomeAssistant,
    entry: ConfigEntry,
    main_subentry_id: str | None,
    fallback: object = None,
) -> str:
    """Prefer the main gateway device name, then the source title."""
    registry = device_registry.async_get(hass)
    candidates = [
        device
        for device in registry.devices.values()
        if device.config_entry_id == entry.entry_id
        and device.config_subentry_id in (None, main_subentry_id)
        and str(device.model or "").startswith("Gateway")
    ]
    if candidates:
        device = sorted(candidates, key=lambda item: item.id)[0]
        if name := device.name_by_user or device.name:
            return name
    return str(fallback or entry.options.get("host") or "Gateway")


def ensure_main_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    title_hint: object = None,
) -> ConfigSubentry:
    """Create or synchronize the managed main-gateway UI subentry."""
    data = _main_gateway_data(entry)
    unique_id = str(data.get("did") or data.get("host") or entry.entry_id)
    subentry = get_main_subentry(entry)
    desired_title = main_gateway_title(
        _main_gateway_name(
            hass,
            entry,
            subentry.subentry_id if subentry is not None else None,
            title_hint,
        )
    )

    if subentry is None:
        subentry = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type=SUBENTRY_MAIN_GATEWAY,
            title=desired_title,
            unique_id=unique_id,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        return subentry

    changes = {}
    if dict(subentry.data) != data:
        changes["data"] = data
    if subentry.unique_id != unique_id:
        changes["unique_id"] = unique_id
    # Keep explicit user renames. Managed titles follow direct reconfiguration.
    if subentry.title.startswith(MAIN_GATEWAY_TITLE_PREFIX) and (
        subentry.title != desired_title
    ):
        changes["title"] = desired_title
    if changes:
        hass.config_entries.async_update_subentry(entry, subentry, **changes)
    return subentry


class GatewaySite:
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/site.py",
    '''        main_options = dict(entry.options)
        main_options.pop(CONF_DEVICE_ROUTES, None)
        self.main = MultiGateway(**main_options)
        self.main.config_subentry_id = None
        self.main.site = self

        self.aux: dict[str, MultiGateway] = {}
        self._titles: dict[str, str] = {}

        self._register_gateway(self.main)
''',
    '''        main_subentry = get_main_subentry(entry)
        if main_subentry is None:
            raise RuntimeError("Gateway site has no main-gateway subentry")
        self.main_subentry_id = main_subentry.subentry_id

        main_options = dict(entry.options)
        main_options.pop(CONF_DEVICE_ROUTES, None)
        self.main = MultiGateway(**main_options)
        self.main.config_subentry_id = self.main_subentry_id
        self.main.site = self

        self.aux: dict[str, MultiGateway] = {}
        self._titles: dict[str, str] = {
            self.main_subentry_id: main_subentry.title
        }

        self._register_gateway(self.main)
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/site.py",
    '''        if gateway.device:
            name = gateway.device.human_name
        elif subentry_id:
            name = self._titles.get(subentry_id, "Aux gateway")
        else:
            name = "Main gateway"
''',
    '''        if gateway.device:
            name = gateway.device.human_name
        elif subentry_id:
            name = self._titles.get(
                subentry_id,
                "Main gateway"
                if subentry_id == self.main_subentry_id
                else "Aux gateway",
            )
        else:
            name = "Main gateway"
''',
)

replace_once(
    "custom_components/xiaomi_gateway3/hass/add_entitites.py",
    '''def handle_add_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    gw: MultiGateway,
    config_subentry_id: str = None,
):
''',
    '''def handle_add_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    gw: MultiGateway,
    config_subentry_id: str = None,
    child_config_subentry_id: str = None,
):
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/add_entitites.py",
    '''            for entity in get_entities(device, gw.stats_domain):
                gw.debug("add_entity", device=device, entity=entity.entity_id)
                add_entity(
                    hass,
                    config_entry,
                    entity,
                    config_subentry_id
                    if device.type == GATEWAY
                    else None,
                )

            # add listener for setup lazy entities (if device has them)
            if remove_listener := handle_lazy_entities(hass, config_entry, device):
''',
    '''            device_subentry_id = (
                config_subentry_id
                if device.type == GATEWAY
                else child_config_subentry_id
            )
            for entity in get_entities(device, gw.stats_domain):
                gw.debug("add_entity", device=device, entity=entity.entity_id)
                add_entity(
                    hass,
                    config_entry,
                    entity,
                    device_subentry_id,
                )

            # add listener for setup lazy entities (if device has them)
            if remove_listener := handle_lazy_entities(
                hass,
                config_entry,
                device,
                device_subentry_id,
            ):
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/add_entitites.py",
    '''                config_subentry_id=(
                    config_subentry_id if device.type == GATEWAY else None
                ),
''',
    '''                config_subentry_id=(
                    config_subentry_id
                    if device.type == GATEWAY
                    else child_config_subentry_id
                ),
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/add_entitites.py",
    '''def handle_lazy_entities(
    hass: HomeAssistant, config_entry: ConfigEntry, device: XDevice
):
''',
    '''def handle_lazy_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device: XDevice,
    config_subentry_id: str = None,
):
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/hass/add_entitites.py",
    '''        add_entity(hass, config_entry, entity)
''',
    '''        add_entity(hass, config_entry, entity, config_subentry_id)
''',
)

replace_once(
    "custom_components/xiaomi_gateway3/hass/entity.py",
    '''        if device.type != GATEWAY:
            via_device = (DOMAIN, device.gateways[0].device.uid)
        else:
            via_device = None
''',
    '''        if device.type != GATEWAY:
            default_gateway = getattr(device, "default_gateway", None)
            parent_gateway = (
                default_gateway
                if default_gateway is not None and default_gateway.device is not None
                else device.gateways[0]
            )
            via_device = (DOMAIN, parent_gateway.device.uid)
        else:
            via_device = None
''',
)

replace_once(
    "custom_components/xiaomi_gateway3/__init__.py",
    '''from .hass.migration import (
    TARGET_MINOR_VERSION,
    TARGET_VERSION,
    async_refresh_migration_issue,
)
from .hass.site import GatewaySite, get_aux_subentries
''',
    '''from .hass.migration import (
    TARGET_MINOR_VERSION,
    TARGET_VERSION,
    assign_unscoped_devices_to_main,
    async_refresh_migration_issue,
)
from .hass.site import (
    GatewaySite,
    ensure_main_subentry,
    get_aux_subentries,
)
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/__init__.py",
    '''        handle_add_entities(
            hass,
            entry,
            gateway,
            config_subentry_id=getattr(
                gateway, "config_subentry_id", None
            ),
        )
''',
    '''        handle_add_entities(
            hass,
            entry,
            gateway,
            config_subentry_id=getattr(
                gateway, "config_subentry_id", None
            ),
            child_config_subentry_id=site.main_subentry_id,
        )
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/__init__.py",
    '''async def _async_prepare_site_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    subentries = get_aux_subentries(entry)
''',
    '''async def _async_prepare_site_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    main_subentry = ensure_main_subentry(hass, entry)
    assign_unscoped_devices_to_main(
        hass, entry.entry_id, main_subentry.subentry_id
    )

    subentries = get_aux_subentries(entry)
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/__init__.py",
    '''    hass.config_entries.async_update_entry(
        config_entry,
        version=TARGET_VERSION,
        minor_version=TARGET_MINOR_VERSION,
    )
''',
    '''    if config_entry.data.get(CONF_SITE):
        main_subentry = ensure_main_subentry(hass, config_entry)
        assign_unscoped_devices_to_main(
            hass, config_entry.entry_id, main_subentry.subentry_id
        )

    hass.config_entries.async_update_entry(
        config_entry,
        version=TARGET_VERSION,
        minor_version=TARGET_MINOR_VERSION,
    )
''',
)

replace_once(
    "custom_components/xiaomi_gateway3/config_flow.py",
    '''    SUBENTRY_AUX_GATEWAY,
    SUPPORTED_MODELS,
)
''',
    '''    SUBENTRY_AUX_GATEWAY,
    SUBENTRY_MAIN_GATEWAY,
    SUPPORTED_MODELS,
)
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/config_flow.py",
    '''from .hass.site import GatewaySite, get_aux_subentries
''',
    '''from .hass.site import (
    GatewaySite,
    get_aux_subentries,
    main_gateway_title,
)
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/config_flow.py",
    '''    VERSION = 5
    MINOR_VERSION = 1
''',
    '''    VERSION = 5
    MINOR_VERSION = 2
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/config_flow.py",
    '''    gateway_defaults: dict = None
''',
    '''    gateway_defaults: dict = None
    main_gateway_name: str = None
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/config_flow.py",
    '''            self.gateway_defaults = {
                "host": device["localip"],
                "token": device["token"],
            }
''',
    '''            self.gateway_defaults = {
                "host": device["localip"],
                "token": device["token"],
            }
            self.main_gateway_name = device["name"]
''',
)
replace_once(
    "custom_components/xiaomi_gateway3/config_flow.py",
    '''                return self.async_create_entry(
                    title=title,
                    data=site_data,
                    options=data,
                )
''',
    '''                return self.async_create_entry(
                    title=title,
                    data=site_data,
                    options=data,
                    subentries=[
                        {
                            "subentry_type": SUBENTRY_MAIN_GATEWAY,
                            "data": data,
                            "title": main_gateway_title(
                                self.main_gateway_name or data["host"]
                            ),
                            "unique_id": str(
                                info.get("did") or data["host"]
                            ),
                        }
                    ],
                )
''',
)
