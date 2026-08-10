import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

translation = ROOT / "custom_components/xiaomi_gateway3/translations/en.json"
data = json.loads(translation.read_text())
subentries = data.setdefault("config_subentries", {})
updated = {"main_gateway": {"entry_type": "Main gateway"}}
updated.update(subentries)
data["config_subentries"] = updated
translation.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

(ROOT / "tests/test_main_gateway_subentry.py").write_text(
    '''from types import MappingProxyType, SimpleNamespace

from custom_components.xiaomi_gateway3.core.const import (
    SUBENTRY_AUX_GATEWAY,
    SUBENTRY_MAIN_GATEWAY,
)
from custom_components.xiaomi_gateway3.hass.site import (
    MAIN_GATEWAY_TITLE_PREFIX,
    get_aux_subentries,
    get_main_subentry,
    main_gateway_title,
    strip_main_gateway_title,
)


def test_main_gateway_title_is_explicit_and_stable():
    title = main_gateway_title("GW-1F")
    assert title == f"{MAIN_GATEWAY_TITLE_PREFIX}GW-1F"
    assert main_gateway_title(title) == title
    assert strip_main_gateway_title(title) == "GW-1F"


def test_main_and_aux_subentries_are_distinct():
    main = SimpleNamespace(subentry_type=SUBENTRY_MAIN_GATEWAY)
    aux = SimpleNamespace(subentry_type=SUBENTRY_AUX_GATEWAY)
    entry = SimpleNamespace(
        subentries=MappingProxyType({"m": main, "a": aux})
    )

    assert get_main_subentry(entry) is main
    assert get_aux_subentries(entry) == (aux,)
'''
)

migration_test = ROOT / "tests/test_migration.py"
text = migration_test.read_text()
text += '''


def test_site_schema_minor_version_includes_main_gateway_subentry():
    from custom_components.xiaomi_gateway3.hass.migration import TARGET_MINOR_VERSION

    assert TARGET_MINOR_VERSION == 2
'''
migration_test.write_text(text)
