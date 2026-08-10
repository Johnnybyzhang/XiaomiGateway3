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
    '''        if current.device_id in device_ids or (
            include_entry_entities
            and current.device_id is None
            and current.config_subentry_id == source_subentry_id
        ) or (
''',
    '''        if current.device_id in device_ids or (
            include_entry_entities
            and current.config_subentry_id == source_subentry_id
        ) or (
''',
)
