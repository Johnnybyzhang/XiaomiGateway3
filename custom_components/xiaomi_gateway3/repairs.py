"""Repairs flows for Xiaomi Gateway 3."""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.repairs import (
    ConfirmRepairFlow,
    RepairsFlow,
    RepairsFlowResult,
)
from homeassistant.core import HomeAssistant

from .hass.migration import (
    ISSUE_LEGACY_CONFIG_MIGRATION,
    MigrationError,
    InterruptedMigration,
    UnsupportedCoreVersionError,
    async_migrate_legacy_site,
    async_refresh_migration_issue,
    async_resume_interrupted_site,
    cloud_entry_label,
    gateway_entry_label,
    interrupted_migration_sites,
    legacy_cloud_entries,
    legacy_gateway_entries,
)

CONF_ACCOUNT = "account"
CONF_MAIN_GATEWAY = "main_gateway"
CONF_AUX_GATEWAYS = "aux_gateways"
CONF_CONFIRM = "confirm"
CONF_INTERRUPTED_SITE = "site"
NO_ACCOUNT = "none"

_LOGGER = logging.getLogger(__name__)


class LegacyConfigMigrationFlow(RepairsFlow):
    """Migrate only the topology explicitly selected by the user."""

    cloud_entry_id: str | None = None
    main_entry_id: str | None = None
    resume_parent_id: str | None = None
    aux_entry_ids: list[str]

    def __init__(self) -> None:
        """Initialize the repair flow."""
        self.aux_entry_ids = []
        super().__init__()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Start the migration wizard without modifying any entry."""
        if interrupted_migration_sites(self.hass):
            return await self.async_step_resume()
        if not legacy_gateway_entries(self.hass):
            await async_refresh_migration_issue(self.hass)
            return self.async_create_entry(data={})
        if legacy_cloud_entries(self.hass):
            return await self.async_step_account()
        return await self.async_step_main_gateway()

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Select an optional existing Mi Cloud entry as the site parent."""
        clouds = legacy_cloud_entries(self.hass)
        choices = {NO_ACCOUNT: "Do not attach a Mi Cloud account"}
        choices.update(
            {entry.entry_id: cloud_entry_label(entry) for entry in clouds}
        )
        default = clouds[0].entry_id if len(clouds) == 1 else NO_ACCOUNT

        if user_input is not None:
            selected = user_input[CONF_ACCOUNT]
            self.cloud_entry_id = None if selected == NO_ACCOUNT else selected
            return await self.async_step_main_gateway()

        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {vol.Required(CONF_ACCOUNT, default=default): vol.In(choices)}
            ),
        )

    async def async_step_main_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Require an explicit main-gateway selection."""
        gateways = legacy_gateway_entries(self.hass)
        if not gateways:
            await async_refresh_migration_issue(self.hass)
            return self.async_create_entry(data={})

        choices = {
            entry.entry_id: gateway_entry_label(entry) for entry in gateways
        }
        oldest = gateways[0]
        if user_input is not None:
            self.main_entry_id = user_input[CONF_MAIN_GATEWAY]
            return await self.async_step_aux_gateways()

        return self.async_show_form(
            step_id="main_gateway",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MAIN_GATEWAY, default=oldest.entry_id
                    ): vol.In(choices)
                }
            ),
            description_placeholders={
                "suggested_main": gateway_entry_label(oldest)
            },
        )

    async def async_step_aux_gateways(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Select which remaining legacy gateways join this site."""
        gateways = [
            entry
            for entry in legacy_gateway_entries(self.hass)
            if entry.entry_id != self.main_entry_id
        ]
        choices = {
            entry.entry_id: gateway_entry_label(entry) for entry in gateways
        }
        if not choices:
            self.aux_entry_ids = []
            return await self.async_step_confirm()

        if user_input is not None:
            self.aux_entry_ids = list(user_input.get(CONF_AUX_GATEWAYS, []))
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="aux_gateways",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AUX_GATEWAYS,
                        default=list(choices),
                    ): cv.multi_select(choices)
                }
            ),
        )

    async def async_step_resume(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Select a partially assembled site to finish explicitly."""
        candidates = interrupted_migration_sites(self.hass)
        if not candidates:
            return await self.async_step_init()

        choices = {
            item.parent.entry_id: (
                f"{item.parent.title} — main: "
                f"{gateway_entry_label(item.main)}"
            )
            for item in candidates
        }
        if len(candidates) == 1 and user_input is None:
            self.resume_parent_id = candidates[0].parent.entry_id
            return await self.async_step_resume_confirm()
        if user_input is not None:
            self.resume_parent_id = user_input[CONF_INTERRUPTED_SITE]
            return await self.async_step_resume_confirm()

        return self.async_show_form(
            step_id="resume",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INTERRUPTED_SITE,
                        default=candidates[0].parent.entry_id,
                    ): vol.In(choices)
                }
            ),
        )

    def _resume_candidate(self) -> InterruptedMigration | None:
        return next(
            (
                item
                for item in interrupted_migration_sites(self.hass)
                if item.parent.entry_id == self.resume_parent_id
            ),
            None,
        )

    async def async_step_resume_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Finish the interrupted topology only after explicit confirmation."""
        candidate = self._resume_candidate()
        if candidate is None:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        if user_input is not None and user_input.get(CONF_CONFIRM):
            try:
                await async_resume_interrupted_site(
                    self.hass, candidate.parent.entry_id
                )
            except UnsupportedCoreVersionError as err:
                _LOGGER.warning("Interrupted migration blocked: %s", err)
                errors["base"] = "unsupported_core"
            except MigrationError as err:
                _LOGGER.error("Interrupted migration failed", exc_info=err)
                errors["base"] = "migration_failed"
            else:
                return self.async_create_entry(data={})

        auxiliaries = [
            gateway_entry_label(source)
            for source, _ in candidate.auxiliaries
        ]
        return self.async_show_form(
            step_id="resume_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM, default=False): bool}
            ),
            description_placeholders={
                "site": candidate.parent.title,
                "main_gateway": gateway_entry_label(candidate.main),
                "aux_gateways": (
                    ", ".join(auxiliaries) if auxiliaries else "None"
                ),
            },
            errors=errors,
        )

    def _selection_summary(self) -> dict[str, str]:
        gateways = {
            entry.entry_id: entry for entry in legacy_gateway_entries(self.hass)
        }
        clouds = {
            entry.entry_id: entry for entry in legacy_cloud_entries(self.hass)
        }
        main = gateways.get(self.main_entry_id)
        auxiliaries = [
            gateway_entry_label(gateways[entry_id])
            for entry_id in self.aux_entry_ids
            if entry_id in gateways
        ]
        cloud = clouds.get(self.cloud_entry_id)
        return {
            "account": cloud_entry_label(cloud) if cloud else "No Mi Cloud account",
            "main_gateway": gateway_entry_label(main) if main else "Unavailable",
            "aux_gateways": ", ".join(auxiliaries) if auxiliaries else "None",
        }

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Apply the selected topology only after explicit confirmation."""
        errors: dict[str, str] = {}
        if user_input is not None and user_input.get(CONF_CONFIRM):
            try:
                assert self.main_entry_id is not None
                await async_migrate_legacy_site(
                    self.hass,
                    cloud_entry_id=self.cloud_entry_id,
                    main_entry_id=self.main_entry_id,
                    aux_entry_ids=self.aux_entry_ids,
                )
            except UnsupportedCoreVersionError as err:
                _LOGGER.warning("Legacy gateway migration blocked: %s", err)
                errors["base"] = "unsupported_core"
            except (MigrationError, AssertionError) as err:
                _LOGGER.error("Legacy gateway migration failed", exc_info=err)
                errors["base"] = "migration_failed"
            else:
                return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM, default=False): bool}
            ),
            description_placeholders=self._selection_summary(),
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a legacy topology issue."""
    if issue_id == ISSUE_LEGACY_CONFIG_MIGRATION:
        return LegacyConfigMigrationFlow()
    return ConfirmRepairFlow()
