from __future__ import annotations

import json
import zipfile
from pathlib import Path

from zhenxun_astr.template_pack import TemplateRegistry


def write_pack(root: Path, *, pack_id: str = "custom", name: str = "自定义") -> None:
    (root / "assets" / "sign" / "img" / "tag").mkdir(parents=True)
    (root / "assets" / "sign" / "img" / "weather").mkdir(parents=True)
    (root / "sign_card.html").write_text(
        "<div>{{ brand_name }}</div>", encoding="utf-8"
    )
    (root / "sign_card.css").write_text(".wrapper{}", encoding="utf-8")
    (root / "assets" / "sign" / "img" / "tag" / "0.png").write_bytes(b"tag")
    (root / "assets" / "sign" / "img" / "weather" / "10.png").write_bytes(b"weather")
    (root / "template.json").write_text(
        json.dumps(
            {
                "format": "zhenxun-astr-template",
                "version": 1,
                "id": pack_id,
                "name": name,
                "card": {"width": 465, "height": 926},
                "files": {
                    "template": "sign_card.html",
                    "style": "sign_card.css",
                    "assets": "assets/sign",
                },
                "settings": {"brand_name": name},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_legacy(root: Path) -> None:
    (root / "assets" / "sign").mkdir(parents=True)
    (root / "sign_card.html").write_text("default", encoding="utf-8")
    (root / "sign_card.css").write_text("default", encoding="utf-8")
    (root / "assets" / "sign" / "placeholder.png").write_bytes(b"default")


def write_zip(source: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for file_path in source.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source).as_posix())


def test_discovers_folder_pack_and_keeps_legacy_default(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    write_legacy(plugin_root)
    write_pack(plugin_root / "template_packs" / "custom")

    packs, errors = TemplateRegistry(plugin_root, tmp_path / "data").discover()

    assert errors == []
    assert set(packs) == {"default", "custom"}
    assert packs["custom"].settings["brand_name"] == "自定义"
    assert packs["custom"].tag_names == ("0.png",)
    assert packs["custom"].weather_names == ("10.png",)
    assert packs["custom"].read_text("sign_card.html").startswith("<div>")


def test_legacy_default_loads_template_owned_settings(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    write_legacy(plugin_root)
    (plugin_root / "template_settings.json").write_text(
        json.dumps(
            {
                "brand_name": "模板品牌",
                "date_format": "%Y-%m-%d",
                "messages": {"sign_title": "模板签到"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack = TemplateRegistry(plugin_root, tmp_path / "data").legacy_pack()

    assert pack.settings["brand_name"] == "模板品牌"
    assert pack.settings["date_format"] == "%Y-%m-%d"
    assert pack.settings["messages"]["sign_title"] == "模板签到"


def test_discovers_zip_with_enclosing_directory(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    source = tmp_path / "source" / "wrapped"
    write_legacy(plugin_root)
    write_pack(source, pack_id="zip-pack", name="ZIP 模板")
    archive_path = plugin_root / "template_packs" / "zip-pack.zip"
    archive_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        for file_path in source.rglob("*"):
            if file_path.is_file():
                archive.write(
                    file_path, f"wrapped/{file_path.relative_to(source).as_posix()}"
                )

    packs, errors = TemplateRegistry(plugin_root, tmp_path / "data").discover()

    assert errors == []
    assert packs["zip-pack"].source_kind == "zip"
    assert packs["zip-pack"].root_prefix == "wrapped"
    assert packs["zip-pack"].read_bytes("assets/sign/img/tag/0.png") == b"tag"


def test_persistent_pack_overrides_bundled_pack_with_same_id(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    persistent_root = tmp_path / "data"
    write_legacy(plugin_root)
    write_pack(plugin_root / "template_packs" / "same", pack_id="same", name="内置")
    write_pack(persistent_root / "same", pack_id="same", name="数据目录")

    packs, errors = TemplateRegistry(plugin_root, persistent_root).discover()

    assert errors == []
    assert packs["same"].name == "数据目录"


def test_unsafe_zip_is_ignored_without_breaking_default(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    write_legacy(plugin_root)
    archive_path = plugin_root / "template_packs" / "unsafe.zip"
    archive_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../template.json", "{}")

    packs, errors = TemplateRegistry(plugin_root, tmp_path / "data").discover()

    assert set(packs) == {"default"}
    assert len(errors) == 1
    assert "路径不安全" in errors[0]


def test_install_zip_is_atomic_idempotent_and_updateable(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    persistent_root = tmp_path / "data" / "template_packs"
    source = tmp_path / "source"
    archive_path = tmp_path / "uploads" / "custom.zip"
    write_legacy(plugin_root)
    write_pack(source, pack_id="tpl_random", name="设置页模板")
    write_zip(source, archive_path)
    registry = TemplateRegistry(plugin_root, persistent_root)

    installed = registry.install_zip(archive_path)
    unchanged = registry.install_zip(archive_path)

    assert installed.action == "installed"
    assert installed.target_path == persistent_root / "tpl_random.zip"
    assert unchanged.action == "unchanged"
    assert unchanged.pack.name == "设置页模板"

    (source / "sign_card.css").write_text(".wrapper{color:red}", encoding="utf-8")
    write_zip(source, archive_path)
    updated = registry.install_zip(archive_path)

    assert updated.action == "updated"
    assert updated.pack.read_text("sign_card.css") == ".wrapper{color:red}"
    assert not any(
        path.name.startswith(".tpl_random-") for path in persistent_root.iterdir()
    )
