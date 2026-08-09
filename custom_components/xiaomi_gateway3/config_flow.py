import base64
import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .core import core_utils
from .core.const import (
    CONF_DEVICE_ROUTES,
    CONF_SITE,
    DOMAIN,
    PID_BLE,
    PID_WIFI,
    PID_WIFI_BLE,
    ROUTE_MAIN,
    SUBENTRY_AUX_GATEWAY,
    SUPPORTED_MODELS,
)
from .core.xiaomi_cloud import AuthResult, MiCloud
from .hass import hass_utils
from .hass.migration import MigrationError, async_promote_aux_gateway
from .hass.site import GatewaySite, get_aux_subentries

_LOGGER = logging.getLogger(__name__)

SERVERS = {
    "cn": "China",
    "de": "Europe",
    "i2": "India",
    "ru": "Russia",
    "sg": "Singapore",
    "us": "United States",
}
GATEWAY_META_KEYS = ("did", "mac", "model")
GATEWAY_DISCOVERY_KEYS = (*GATEWAY_META_KEYS, "key")


def vol_schema(schema: dict, defaults: dict = None) -> vol.Schema:
    if defaults:
        for key in schema:
            if (value := defaults.get(key.schema)) is not None:
                key.default = vol.default_factory(value)
    return vol.Schema(schema)


def gateway_schema(defaults: dict = None) -> vol.Schema:
    return vol_schema(
        {
            vol.Required("host"): str,
            vol.Required("token"): str,
            vol.Optional("key"): str,
            vol.Optional("stats"): vol.In(
                {
                    False: "Disabled",
                    True: "Sensors",
                    "binary_sensor": "Binary sensors",
                }
            ),
            vol.Optional("debug"): cv.multi_select(
                {
                    "true": "Basic logs",
                    "mqtt": "MQTT logs",
                    "zigbee": "Zigbee logs",
                }
            ),
        },
        defaults,
    )


async def validate_gateway(
    hass, user_input: dict[str, Any]
) -> tuple[dict[str, Any], dict | None]:
    """Validate gateway credentials and persist discovered identity data."""
    data = dict(user_input)

    if not data.get("key"):
        if key := await hass_utils.restore_gateway_key(hass, data["token"]):
            data["key"] = key
        else:
            data.pop("key", None)

    info = await core_utils.gateway_info(
        data["host"], data["token"], data.get("key")
    )
    if info and "error" not in info:
        for key in GATEWAY_DISCOVERY_KEYS:
            if value := info.get(key):
                data[key] = value

    return data, info


class FlowHandler(ConfigFlow, domain=DOMAIN):
    VERSION = 5
    MINOR_VERSION = 1

    cloud: MiCloud = None
    cloud_user_input: dict = None
    cloud_gateways: list[dict] = None
    site_data: dict = None
    gateway_defaults: dict = None

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        if config_entry.data.get(CONF_SITE):
            return {SUBENTRY_AUX_GATEWAY: AuxGatewaySubentryFlow}
        return {}

    async def async_step_user(self, user_input: dict = None):
        if user_input:
            if user_input["action"] == "cloud":
                return await self.async_step_cloud()
            self.site_data = {CONF_SITE: True}
            return await self.async_step_token()

        actions = {
            "cloud": "Add Mi Cloud Account and Main Gateway",
            "token": "Add Main Gateway using Token",
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("action", default="cloud"): vol.In(actions)}
            ),
        )

    def _show_cloud_form(self, defaults: dict, errors: dict = None):
        return self.async_show_form(
            step_id="cloud",
            data_schema=vol_schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): str,
                    vol.Required("servers", default=["cn"]): cv.multi_select(
                        SERVERS
                    ),
                },
                defaults,
            ),
            errors=errors,
        )

    async def _process_cloud_result(self, result: AuthResult):
        if result["ok"]:
            self.cloud.servers = self.cloud_user_input["servers"]
            self.site_data = {
                CONF_SITE: True,
                "username": self.cloud_user_input["username"],
                "servers": self.cloud_user_input["servers"],
                "token": result["token"],
            }
            try:
                self.cloud.devices = await self.cloud.get_devices() or []
            except Exception as err:
                _LOGGER.error("Can't load gateways from Mi Cloud", exc_info=err)
                return self._show_cloud_form(
                    self.cloud_user_input, {"base": "cant_login"}
                )

            self.cloud_gateways = [
                device
                for device in self.cloud.devices
                if device["model"] in SUPPORTED_MODELS
            ]
            if not self.cloud_gateways:
                return self._show_cloud_form(
                    self.cloud_user_input, {"base": "no_gateways"}
                )
            return await self.async_step_main_gateway()

        if image := result.get("captcha"):
            image = "data:image/jpeg;base64," + base64.b64encode(image).decode()
            return self.async_show_form(
                step_id="cloud_captcha",
                data_schema=vol_schema({vol.Required("code"): str}),
                description_placeholders={"image": image},
            )

        if verify := result.get("verify"):
            return self.async_show_form(
                step_id="cloud_verify",
                data_schema=vol_schema({vol.Required("code"): str}),
                description_placeholders={"address": verify},
            )

        _LOGGER.error("Can't login", exc_info=result["exception"])
        return self._show_cloud_form(
            self.cloud_user_input, errors={"base": "cant_login"}
        )

    async def async_step_cloud(self, user_input: dict = None):
        if not user_input:
            return self._show_cloud_form(user_input)
        if not user_input["servers"]:
            return self._show_cloud_form(user_input, {"base": "no_servers"})

        if not self.cloud:
            self.cloud = MiCloud(async_create_clientsession(self.hass))
        self.cloud_user_input = user_input
        result = await self.cloud.login(user_input["username"], user_input["password"])
        return await self._process_cloud_result(result)

    async def async_step_cloud_captcha(self, user_input: dict = None):
        result = await self.cloud.login_captcha(user_input["code"])
        return await self._process_cloud_result(result)

    async def async_step_cloud_verify(self, user_input: dict = None):
        result = await self.cloud.login_verify(user_input["code"])
        return await self._process_cloud_result(result)

    async def async_step_main_gateway(self, user_input: dict = None):
        gateways = {
            device["did"]: f"{device['name']} ({device['localip']})"
            for device in self.cloud_gateways
        }
        if user_input:
            device = next(
                item
                for item in self.cloud_gateways
                if item["did"] == user_input["did"]
            )
            self.gateway_defaults = {
                "host": device["localip"],
                "token": device["token"],
            }
            return await self.async_step_token()

        return self.async_show_form(
            step_id="main_gateway",
            data_schema=vol.Schema({vol.Required("did"): vol.In(gateways)}),
        )

    async def async_step_token(self, user_input: dict = None):
        kwargs = {
            "description_placeholders": {
                "link": "https://github.com/AlexxIT/XiaomiGateway3#obtain-mi-home-device-token",
            },
        }

        if user_input:
            data, info = await validate_gateway(self.hass, user_input)
            if info and "error" not in info:
                unique_id = self._site_unique_id(info, data["host"])
                site_data = self.site_data or {CONF_SITE: True}
                title = site_data.get("username") or data["host"]

                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=title,
                    data=site_data,
                    options=data,
                )

            kwargs["errors"] = {
                "base": info.get("error", "cant_connect")
                if info
                else "cant_connect"
            }

        defaults = user_input or self.gateway_defaults
        data_schema = vol_schema(
            {
                vol.Required("host"): str,
                vol.Required("token"): str,
                vol.Optional("key"): str,
            },
            defaults,
        )
        return self.async_show_form(
            step_id="token", data_schema=data_schema, **kwargs
        )

    def _site_unique_id(self, gateway_info: dict, host: str) -> str:
        if self.site_data and self.site_data.get("username"):
            servers = ",".join(sorted(self.site_data.get("servers", [])))
            return f"account:{self.site_data['username']}:{servers}"
        return f"gateway:{gateway_info.get('did') or host}"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlow):
    cloud_devices = None
    route_device_uid: str = None
    promote_subentry_id: str = None

    @property
    def config_entry(self):
        return self.hass.config_entries.async_get_entry(self.handler)

    @property
    def runtime(self):
        return self.hass.data[DOMAIN][self.config_entry.entry_id]

    @property
    def site(self) -> GatewaySite | None:
        runtime = self.runtime
        return runtime if isinstance(runtime, GatewaySite) else None

    @property
    def cloud(self) -> MiCloud:
        runtime = self.runtime
        if isinstance(runtime, GatewaySite):
            return runtime.cloud
        return runtime

    async def async_step_init(self, user_input: dict = None):
        if self.config_entry.data.get(CONF_SITE):
            return await self.async_step_site()
        if self.config_entry.data:
            return await self.async_step_cloud()
        return await self.async_step_user()

    async def async_step_site(self, user_input: dict = None):
        actions = {
            "gateway": "Configure Main Gateway",
            "routes": "Configure Device Routing",
        }
        if get_aux_subentries(self.config_entry):
            actions["change_main"] = "Change Main Gateway"
        if self.site and self.site.cloud:
            actions["cloud"] = "MiCloud Devices Info"

        if user_input:
            action = user_input["action"]
            if action == "gateway":
                return await self.async_step_user()
            if action == "routes":
                return await self.async_step_device_routes()
            if action == "change_main":
                return await self.async_step_change_main()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="site",
            data_schema=vol.Schema(
                {vol.Required("action", default="gateway"): vol.In(actions)}
            ),
        )

    async def async_step_change_main(self, user_input: dict = None):
        subentries = get_aux_subentries(self.config_entry)
        if not subentries:
            return self.async_abort(reason="no_aux_gateways")

        choices = {
            subentry.subentry_id: (
                f"{subentry.title} ({subentry.data.get('host', 'unknown host')})"
            )
            for subentry in subentries
        }
        if user_input:
            self.promote_subentry_id = user_input["gateway"]
            return await self.async_step_change_main_confirm()

        return self.async_show_form(
            step_id="change_main",
            data_schema=vol.Schema(
                {vol.Required("gateway"): vol.In(choices)}
            ),
        )

    async def async_step_change_main_confirm(self, user_input: dict = None):
        selected = self.config_entry.subentries.get(self.promote_subentry_id)
        if selected is None:
            return self.async_abort(reason="no_aux_gateways")

        errors = None
        if user_input and user_input.get("confirm"):
            try:
                await async_promote_aux_gateway(
                    self.hass,
                    self.config_entry,
                    self.promote_subentry_id,
                )
            except MigrationError:
                errors = {"base": "migration_failed"}
            else:
                return self.async_abort(reason="main_gateway_changed")

        return self.async_show_form(
            step_id="change_main_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): bool}
            ),
            description_placeholders={
                "gateway": (
                    f"{selected.title} "
                    f"({selected.data.get('host', 'unknown host')})"
                )
            },
            errors=errors,
        )

    async def async_step_cloud(self, user_input: dict = None):
        if self.cloud_devices is None:
            devices = {}
            for device in self.cloud.devices or []:
                if device["pid"] in (PID_WIFI, PID_WIFI_BLE):
                    info = device["localip"]
                elif device["pid"] == PID_BLE:
                    info = device["mac"]
                else:
                    continue
                devices[device["did"]] = f"{device['name']} ({info})"
            self.cloud_devices = dict(
                sorted(devices.items(), key=lambda item: item[1])
            )

        if user_input:
            did = user_input["did"]
            device = next(i for i in self.cloud.devices if i["did"] == did)
            info = await core_utils.get_device_info(self.cloud, device)
            device_info = "\n".join(f"{k}: {v}" for k, v in info.items())
        elif self.cloud_devices:
            device_info = "Choose a device from the list"
        else:
            device_info = "No devices in account"

        data = vol_schema(
            {vol.Required("did"): vol.In(self.cloud_devices)}, user_input
        )
        return self.async_show_form(
            step_id="cloud",
            data_schema=data,
            description_placeholders={"device_info": device_info},
        )

    async def async_step_user(self, user_input: dict = None):
        errors = None
        if user_input:
            if self.config_entry.data.get(CONF_SITE):
                data, info = await validate_gateway(self.hass, user_input)
                if info and "error" not in info:
                    preserved = {
                        key: self.config_entry.options[key]
                        for key in (CONF_DEVICE_ROUTES, *GATEWAY_META_KEYS)
                        if key in self.config_entry.options
                    }
                    return self.async_create_entry(
                        title="", data=preserved | data
                    )

                errors = {
                    "base": info.get("error", "cant_connect")
                    if info
                    else "cant_connect"
                }
            else:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=gateway_schema(
                user_input or dict(self.config_entry.options)
            ),
            errors=errors,
        )

    async def async_step_device_routes(self, user_input: dict = None):
        site = self.site
        devices = (
            {
                device.uid: f"{device.human_name} ({device.uid})"
                for device in site.devices
            }
            if site
            else {}
        )
        if not devices:
            return self.async_abort(reason="no_devices")

        if user_input:
            self.route_device_uid = user_input["device"]
            return await self.async_step_device_route()

        return self.async_show_form(
            step_id="device_routes",
            data_schema=vol.Schema(
                {vol.Required("device"): vol.In(devices)}
            ),
        )

    async def async_step_device_route(self, user_input: dict = None):
        site = self.site
        device = next(
            item for item in site.devices if item.uid == self.route_device_uid
        )
        choices = site.gateway_options(device)
        routes = dict(self.config_entry.options.get(CONF_DEVICE_ROUTES, {}))
        current = routes.get(device.uid, ROUTE_MAIN)
        if current not in choices:
            choices[current] = "Unavailable auxiliary gateway"

        if user_input:
            selected = user_input["gateway"]
            if selected == ROUTE_MAIN:
                routes.pop(device.uid, None)
            else:
                routes[device.uid] = selected

            options = dict(self.config_entry.options)
            if routes:
                options[CONF_DEVICE_ROUTES] = routes
            else:
                options.pop(CONF_DEVICE_ROUTES, None)
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="device_route",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "gateway", default=current
                    ): vol.In(choices)
                }
            ),
            description_placeholders={"device": device.human_name},
        )


class AuxGatewaySubentryFlow(ConfigSubentryFlow):
    async def _validate(self, user_input: dict) -> tuple[dict, dict | None]:
        return await validate_gateway(self.hass, user_input)

    def _is_duplicate(
        self,
        data: dict,
        info: dict,
        current_subentry_id: str | None = None,
    ) -> bool:
        entry = self._get_entry()
        did = info.get("did")

        if (
            data["host"] == entry.options.get("host")
            or (did and did == entry.options.get("did"))
        ):
            return True

        for subentry in get_aux_subentries(entry):
            if subentry.subentry_id == current_subentry_id:
                continue
            if (
                data["host"] == subentry.data.get("host")
                or (did and did == subentry.data.get("did"))
            ):
                return True

        return False

    async def async_step_user(self, user_input: dict = None):
        errors = None
        if user_input:
            data, info = await self._validate(user_input)
            if info and "error" not in info:
                if self._is_duplicate(data, info):
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title=data["host"],
                    data=data,
                    unique_id=str(info.get("did") or data["host"]),
                )
            errors = {
                "base": info.get("error", "cant_connect")
                if info
                else "cant_connect"
            }

        return self.async_show_form(
            step_id="user",
            data_schema=gateway_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict = None):
        subentry = self._get_reconfigure_subentry()
        errors = None
        if user_input:
            data, info = await self._validate(user_input)
            if info and "error" not in info:
                if self._is_duplicate(data, info, subentry.subentry_id):
                    return self.async_abort(reason="already_configured")
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=data["host"],
                    data=data,
                    unique_id=str(info.get("did") or data["host"]),
                )
            errors = {
                "base": info.get("error", "cant_connect")
                if info
                else "cant_connect"
            }

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=gateway_schema(
                user_input or dict(subentry.data)
            ),
            errors=errors,
        )
