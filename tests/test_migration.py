from types import SimpleNamespace

from custom_components.xiaomi_gateway3.core.const import CONF_DEVICE_ROUTES
from custom_components.xiaomi_gateway3.hass.migration import (
    _gateway_root_ids,
    _promoted_main_options,
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
