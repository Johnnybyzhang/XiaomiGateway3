from types import SimpleNamespace

from custom_components.xiaomi_gateway3.core.const import (
    CONF_DEVICE_ROUTES,
    CONF_SITE,
    SUBENTRY_AUX_GATEWAY,
)
import custom_components.xiaomi_gateway3.hass.migration as migration_module
from custom_components.xiaomi_gateway3.hass.migration import (
    MINIMUM_HA_VERSION,
    _move_entity,
    _move_entry_registry,
    _entity_update_kwargs,
    _gateway_options_match,
    _gateway_root_ids,
    _promoted_main_options,
    _registry_api_supported,
    interrupted_migration_sites,
    is_registry_migration_supported,
    legacy_gateway_entries,
)


def test_oldest_gateway_is_only_a_deterministic_suggestion():
    newer = SimpleNamespace(
        data={},
        options={"host": "192.0.2.2"},
        created_at=2,
        entry_id="newer",
    )
    older = SimpleNamespace(
        data={},
        options={"host": "192.0.2.1"},
        created_at=1,
        entry_id="older",
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [newer, older]
        )
    )

    entries = legacy_gateway_entries(hass)
    assert [entry.entry_id for entry in entries] == ["older", "newer"]
    assert newer.options["host"] == "192.0.2.2"
    assert older.options["host"] == "192.0.2.1"


def test_gateway_root_detection_prefers_model_metadata():
    root = SimpleNamespace(
        id="root",
        model="Gateway: lumi.gateway.mgl03",
        via_device_id=None,
        connections=set(),
    )
    unrelated = SimpleNamespace(
        id="other", model="Sensor", via_device_id=None, connections=set()
    )
    child = SimpleNamespace(
        id="child", model="Sensor", via_device_id="root", connections=set()
    )
    assert _gateway_root_ids([root, unrelated, child]) == {"root"}


def test_promoting_main_preserves_other_routes_only():
    current = {
        "host": "old-main",
        "token": "old-token",
        CONF_DEVICE_ROUTES: {
            "device-on-new-main": "selected-aux",
            "device-on-other-aux": "other-aux",
        },
    }
    promoted = {"host": "new-main", "token": "new-token"}

    result = _promoted_main_options(current, promoted, "selected-aux")

    assert result["host"] == "new-main"
    assert result["token"] == "new-token"
    assert result[CONF_DEVICE_ROUTES] == {
        "device-on-other-aux": "other-aux"
    }



def test_pre_2026_8_registry_model_is_rejected():
    assert not _registry_api_supported(
        {"config_entries", "config_entries_subentries"},
        {"add_config_entry_id", "remove_config_entry_id"},
    )


def test_2026_8_registry_model_is_accepted():
    assert MINIMUM_HA_VERSION == "2026.8.0"
    assert _registry_api_supported(
        {"config_entry_id", "config_subentry_id"},
        {"new_config_entry_id", "new_config_subentry_id"},
    )
    assert is_registry_migration_supported()



def test_gateway_options_match_requires_stable_identity():
    assert _gateway_options_match(
        {"did": "1", "host": "old", "token": "a"},
        {"did": "1", "host": "new", "token": "b"},
    )
    assert _gateway_options_match(
        {"host": "192.0.2.1", "token": "same"},
        {"host": "192.0.2.1", "token": "same"},
    )
    assert not _gateway_options_match(
        {"host": "192.0.2.1", "token": "one"},
        {"host": "192.0.2.1", "token": "two"},
    )


def test_interrupted_cloud_parent_is_detected_for_explicit_resume():
    main_options = {"host": "192.0.2.1", "token": "main", "did": "1"}
    aux_options = {"host": "192.0.2.2", "token": "aux", "did": "2"}
    main = SimpleNamespace(
        data={},
        options=main_options,
        created_at=1,
        entry_id="main",
        title="Main",
    )
    auxiliary = SimpleNamespace(
        data={},
        options=aux_options,
        created_at=2,
        entry_id="aux",
        title="Aux",
    )
    subentry = SimpleNamespace(
        data=aux_options,
        subentry_id="subentry",
        subentry_type=SUBENTRY_AUX_GATEWAY,
        title="Aux",
    )
    parent = SimpleNamespace(
        data={
            CONF_SITE: True,
            "username": "account",
            "token": "cloud-token",
        },
        options=main_options,
        subentries={"subentry": subentry},
        created_at=0,
        entry_id="parent",
        title="Account Site",
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [parent, main, auxiliary]
        )
    )

    candidates = interrupted_migration_sites(hass)

    assert len(candidates) == 1
    assert candidates[0].parent is parent
    assert candidates[0].main is main
    assert candidates[0].auxiliaries == ((auxiliary, subentry),)


class _FakeEntityRegistry:
    def __init__(self, entities):
        self.entities = {entity.entity_id: entity for entity in entities}

    def async_get(self, entity_id):
        return self.entities.get(entity_id)

    def async_get_entity_id(self, domain, platform, unique_id):
        for entity in self.entities.values():
            if (
                entity.domain == domain
                and entity.platform == platform
                and entity.unique_id == unique_id
            ):
                return entity.entity_id
        return None

    def async_update_entity(self, entity_id, **update):
        entity = self.entities[entity_id]
        for key, value in update.items():
            setattr(entity, key, value)
        return entity


class _FakeDeviceRegistry:
    def __init__(self, devices, entities):
        self.devices = {device.id: device for device in devices}
        self._entities = entities

    def async_get(self, device_id):
        return self.devices.get(device_id)

    def async_get_device_by_identifier(self, identifier, entry_id):
        return next(
            (
                device
                for device in self.devices.values()
                if device.config_entry_id == entry_id
                and identifier in device.identifiers
            ),
            None,
        )

    def async_get_device_by_connection(self, connection, entry_id):
        return next(
            (
                device
                for device in self.devices.values()
                if device.config_entry_id == entry_id
                and connection in device.connections
            ),
            None,
        )

    def async_update_device(self, device_id, **update):
        device = self.devices[device_id]
        old_entry_id = device.config_entry_id
        old_subentry_id = device.config_subentry_id
        if "new_config_entry_id" in update:
            device.config_entry_id = update["new_config_entry_id"]
        if "new_config_subentry_id" in update:
            device.config_subentry_id = update["new_config_subentry_id"]
        if "via_device_id" in update:
            device.via_device_id = update["via_device_id"]

        # Reproduce Core 2026.8's synchronous entity cleanup callback.
        for entity in list(self._entities.entities.values()):
            if entity.device_id != device_id:
                continue
            if (
                old_entry_id != device.config_entry_id
                and entity.config_entry_id == old_entry_id
            ) or (
                old_subentry_id != device.config_subentry_id
                and entity.config_entry_id == device.config_entry_id
                and entity.config_subentry_id == old_subentry_id
            ):
                self._entities.entities.pop(entity.entity_id)
        return device

    def async_remove_device(self, device_id):
        self.devices.pop(device_id, None)


def _fake_device(device_id, entry_id, subentry_id=None):
    return SimpleNamespace(
        id=device_id,
        config_entry_id=entry_id,
        config_subentry_id=subentry_id,
        model="Gateway: lumi.gateway.mgl03",
        connections=set(),
        identifiers={("xiaomi_gateway3", device_id)},
        via_device_id=None,
        labels=set(),
        area_id=None,
        name_by_user=None,
        disabled_by=None,
    )


def _fake_entity(entity_id, entry_id, device_id):
    return SimpleNamespace(
        entity_id=entity_id,
        domain="select",
        platform="xiaomi_gateway3",
        unique_id=entity_id.removeprefix("select."),
        config_entry_id=entry_id,
        config_subentry_id=None,
        device_id=device_id,
    )


def test_entity_moves_before_core_device_cleanup(monkeypatch):
    entity = _fake_entity("select.gateway_command", "source", "gateway")
    ent_reg = _FakeEntityRegistry([entity])
    device = _fake_device("gateway", "source")
    dev_reg = _FakeDeviceRegistry([device], ent_reg)

    monkeypatch.setattr(
        migration_module.device_registry, "async_get", lambda hass: dev_reg
    )
    monkeypatch.setattr(
        migration_module.entity_registry, "async_get", lambda hass: ent_reg
    )

    roots = _move_entry_registry(
        SimpleNamespace(),
        "source",
        "target",
        gateway_subentry_id=None,
    )

    assert roots == {"gateway"}
    assert ent_reg.async_get("select.gateway_command") is entity
    assert entity.config_entry_id == "target"
    assert entity.device_id == "gateway"
    assert device.config_entry_id == "target"


def test_retry_finishes_entity_left_on_already_moved_device(monkeypatch):
    device = _fake_device("gateway", "target")
    entity = _fake_entity("select.gateway_command", "source", "gateway")
    ent_reg = _FakeEntityRegistry([entity])
    dev_reg = _FakeDeviceRegistry([device], ent_reg)

    monkeypatch.setattr(
        migration_module.device_registry, "async_get", lambda hass: dev_reg
    )
    monkeypatch.setattr(
        migration_module.entity_registry, "async_get", lambda hass: ent_reg
    )

    roots = _move_entry_registry(
        SimpleNamespace(),
        "source",
        "target",
        gateway_subentry_id=None,
    )

    assert roots == {"gateway"}
    assert entity.config_entry_id == "target"
    assert entity.device_id == "gateway"


def test_missing_snapshot_resolves_live_entity_by_unique_id():
    snapshot = _fake_entity("select.old_name", "source", "gateway")
    live = _fake_entity("select.new_name", "source", "gateway")
    live.unique_id = snapshot.unique_id
    registry = _FakeEntityRegistry([live])

    assert _move_entity(registry, snapshot, "target", None, "gateway")
    assert live.config_entry_id == "target"



def test_entry_change_restates_existing_subentry():
    current = SimpleNamespace(
        config_entry_id="source",
        config_subentry_id="same-subentry",
        device_id="device",
    )

    update = _entity_update_kwargs(
        current,
        "target",
        "same-subentry",
        "device",
    )

    assert update == {
        "config_entry_id": "target",
        "config_subentry_id": "same-subentry",
    }


def test_interrupted_token_only_site_is_recoverable():
    main_options = {"host": "192.0.2.1", "token": "main", "did": "1"}
    aux_options = {"host": "192.0.2.2", "token": "aux", "did": "2"}
    auxiliary = SimpleNamespace(
        data={},
        options=aux_options,
        created_at=2,
        entry_id="aux",
        title="Aux",
    )
    subentry = SimpleNamespace(
        data=aux_options,
        subentry_id="subentry",
        subentry_type=SUBENTRY_AUX_GATEWAY,
        title="Aux",
    )
    parent = SimpleNamespace(
        data={CONF_SITE: True},
        options=main_options,
        subentries={"subentry": subentry},
        created_at=1,
        entry_id="parent",
        title="Token-only Site",
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [parent, auxiliary]
        )
    )

    candidates = interrupted_migration_sites(hass)

    assert len(candidates) == 1
    assert candidates[0].parent is parent
    assert candidates[0].main is parent
    assert candidates[0].auxiliaries == ((auxiliary, subentry),)
