from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from astrbot.dashboard.services.config_service import ConfigFileService, validate_config
from zhenxun_astr.main import ZhenxunSign
from zhenxun_astr.template_pack import TemplateRegistry


class FakeUpload:
    def __init__(self, filename: str, payload: bytes) -> None:
        self.filename = filename
        self.payload = payload
        self.content_length = len(payload)

    async def save(self, target_path: str) -> None:
        Path(target_path).write_bytes(self.payload)


def write_template_zip(archive_path: Path) -> None:
    manifest = {
        "format": "zhenxun-astr-template",
        "version": 1,
        "id": "tpl_settings_upload",
        "name": "设置页上传模板",
        "card": {"width": 465, "height": 926},
        "files": {
            "template": "sign_card.html",
            "style": "sign_card.css",
            "assets": "assets/sign",
        },
        "settings": {},
    }
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "template.json",
            json.dumps(manifest, ensure_ascii=False),
        )
        archive.writestr("sign_card.html", "<div>{{ user.name }}</div>")
        archive.writestr("sign_card.css", ".wrapper{}")
        archive.writestr("assets/sign/img/weather/0.png", b"weather")


def build_plugin(tmp_path: Path, packages: list[str]) -> ZhenxunSign:
    plugin = ZhenxunSign(
        object(),
        {
            "template_management": {
                "packages": packages,
                "activate_latest": True,
            }
        },
    )
    plugin.data_dir = tmp_path / "plugin_data"
    plugin.active_template_path = plugin.data_dir / "active_template.txt"
    plugin.template_registry = TemplateRegistry(
        plugin.plugin_root,
        plugin.data_dir / "template_packs",
    )
    return plugin


def test_settings_upload_installs_and_activates_template(tmp_path: Path) -> None:
    relative_path = "files/template_management/packages/custom.zip"
    plugin = build_plugin(tmp_path, [relative_path])
    write_template_zip(plugin.data_dir / relative_path)

    plugin._import_configured_template_packages()

    installed_path = plugin.data_dir / "template_packs" / "tpl_settings_upload.zip"
    assert installed_path.is_file()
    assert plugin._selected_template_id() == "tpl_settings_upload"
    assert plugin._template_import_report["installed"] == ["设置页上传模板"]
    assert plugin._template_import_report["activated"] == "设置页上传模板"

    plugin._save_active_template_id("default")
    plugin._import_configured_template_packages()

    assert plugin._selected_template_id() == "default"
    assert plugin._template_import_report["unchanged"] == ["设置页上传模板"]
    assert plugin._template_import_report["activated"] == ""


def test_settings_upload_rejects_paths_outside_upload_area(tmp_path: Path) -> None:
    plugin = build_plugin(
        tmp_path,
        ["files/template_management/packages/../../outside.zip"],
    )

    plugin._import_configured_template_packages()

    assert len(plugin._template_import_report["errors"]) == 1
    assert "不属于模板管理区" in plugin._template_import_report["errors"][0]
    assert not (plugin.data_dir / "template_packs").exists()


def test_schema_exposes_native_zip_uploader() -> None:
    schema_path = Path(__file__).parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    management = schema["template_management"]

    assert management["type"] == "object"
    assert management["items"]["packages"]["type"] == "file"
    assert management["items"]["packages"]["file_types"] == ["zip"]
    assert management["items"]["activate_latest"]["default"] is True

    visual_settings = {
        "avatar_url",
        "brand_name",
        "date_format",
        "late_night_messages",
        "messages",
        "morning_messages",
        "reserved_panel",
    }
    assert visual_settings.isdisjoint(schema)

    def visible_texts(value):
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in {"description", "hint"} and isinstance(item, str):
                yield item
            elif isinstance(item, dict):
                yield from visible_texts(item)

    assert max(map(len, visible_texts(schema))) <= 14


def test_astrbot_native_upload_path_installs_end_to_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_archive = tmp_path / "source.zip"
    write_template_zip(source_archive)
    plugin = build_plugin(tmp_path, [])
    schema_path = Path(__file__).parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    config = SimpleNamespace(schema=schema)
    service = ConfigFileService(SimpleNamespace())
    monkeypatch.setattr(
        service,
        "resolve_config_file_scope",
        lambda **_kwargs: (
            "plugin",
            "astrbot_plugin_zhenxun_sign",
            "template_management.packages",
            SimpleNamespace(),
            config,
        ),
    )
    monkeypatch.setattr(service, "_plugin_root_path", lambda _name: plugin.data_dir)

    upload_result = asyncio.run(
        service.upload_config_file(
            scope="plugin",
            name="astrbot_plugin_zhenxun_sign",
            key_path="template_management.packages",
            files={
                "file0": FakeUpload("custom.zip", source_archive.read_bytes()),
            },
        )
    )
    plugin.config["template_management"]["packages"] = upload_result["uploaded"]
    errors, normalised = validate_config(plugin.config, schema, is_core=False)
    plugin.config = normalised

    plugin._import_configured_template_packages()

    assert errors == []
    assert upload_result == {
        "uploaded": ["files/template_management/packages/custom.zip"],
        "errors": [],
    }
    assert (plugin.data_dir / "template_packs" / "tpl_settings_upload.zip").is_file()
    assert plugin._selected_template_id() == "tpl_settings_upload"
