from types import SimpleNamespace

from custom_components.xiaomi_gateway3.core.const import (
    CONF_DEVICE_ROUTES,
    CONF_SITE,
    SUBENTRY_AUX_GATEWAY,
)
from custom_components.xiaomi_gateway3.hass.migration import (
    MINIMUM_HA_VERSION,
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
    assert MINIMUM_HA_VERSION == "2026.8.1"
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
