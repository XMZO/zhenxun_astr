from __future__ import annotations

import re

from zhenxun_astr.template_editor import editor_server
from zhenxun_astr.template_pack import TemplateRegistry


def test_default_editor_state_renders_without_unresolved_template_fields() -> None:
    state = editor_server._load_default_state()
    rendered = editor_server._render_state(state)

    assert '<div class="wrapper">' in rendered
    assert "{{" not in rendered
    assert "/source-assets/img/weather/0.png" in rendered
    assert "自定义文字" not in rendered


def test_layer_override_does_not_replace_theme_defaults() -> None:
    state = editor_server._load_default_state()
    state["theme"]["primary"] = "#22AA88"
    state["layers"]["title"]["x"] = 24

    css = editor_server._build_override_css(state)

    assert "--color-sign-primary: #22AA88" in css
    assert "translate(24px, 0px)" in css
    assert ".wrapper {" not in css


def test_resized_material_dimensions_are_written_to_generated_css() -> None:
    state = editor_server._load_default_state()
    state["layers"]["mainCharacter"]["width"] = 180
    state["layers"]["mainCharacter"]["height"] = 318

    css = editor_server._build_override_css(state)

    assert ".zx-img {" in css
    assert "width: 180px !important" in css
    assert "height: 318px !important" in css


def test_resized_avatar_cannot_be_shrunk_by_header_flex_layout() -> None:
    state = editor_server._load_default_state()
    state["layers"]["avatar"]["width"] = 200
    state["layers"]["avatar"]["height"] = 200

    css = editor_server._build_override_css(state)

    assert "flex-shrink: 0 !important" in css
    assert ".avatar-img { width: 200px !important; height: 200px !important; }" in css


def test_runtime_config_does_not_include_editor_only_theme() -> None:
    state = editor_server._load_default_state()

    config = editor_server._runtime_config(state, avatar_path=None)

    assert "editor_theme" not in config
    assert config["date_format"] == "iso"


def test_custom_image_layer_is_embedded_for_generated_templates() -> None:
    state = editor_server._load_default_state()
    state["customLayers"] = [
        {
            "id": "custom-image-test",
            "label": "测试图片",
            "kind": "image",
            "imageRef": None,
            "x": 0,
            "y": 0,
            "width": 80,
            "height": 80,
            "rotation": 0,
            "scaleX": 1,
            "scaleY": 1,
            "opacity": 1,
            "zIndex": 1200,
            "visible": True,
            "backgroundColor": "",
            "borderRadius": 0,
            "fontSize": 24,
            "fontFamily": "cr105Font",
            "fontWeight": "normal",
            "lineHeight": 1.2,
            "letterSpacing": 0,
            "textAlign": "left",
            "color": "#D47E8F",
            "objectFit": "contain",
            "filter": "none",
            "boxShadow": "",
        }
    ]

    markup = editor_server._custom_layer_markup(state, generated=True)

    assert "data:image/png;base64," in markup
    assert "/source-assets/" not in markup


def test_editor_surface_keeps_selection_overlay_and_launcher() -> None:
    index = (editor_server.EDITOR_ROOT / "index.html").read_text(encoding="utf-8")
    script = (editor_server.EDITOR_ROOT / "editor.js").read_text(encoding="utf-8")

    assert 'id="selectionOverlay"' in index
    assert "function updateSelectionOverlay()" in script
    assert "function syncFrameSelection()" in script
    assert "function selectAsset(assetKey)" in script
    assert "data-asset-select" in script
    assert "function updateHistoryTransaction(before, transactionIndex)" in script
    assert 'data-resize-handle="se"' in index
    assert "function startResize(event)" in script
    assert 'data-bind="templateMeta.id"' not in index
    assert "内部标识由生成器随机创建" in index
    assert (editor_server.EDITOR_ROOT / "start_editor.cmd").is_file()


def test_generator_installs_self_contained_folder_and_zip(tmp_path) -> None:
    state = editor_server._load_default_state()
    state["templateMeta"] = {
        "name": "夏日模板",
        "description": "测试模板包",
    }
    state["content"]["brandName"] = "夏娜"
    state["content"]["dateFormat"] = "%Y年%m月%d日"

    generated = editor_server._generate_bundle(
        state,
        output_root=tmp_path / "output",
        install_root=tmp_path / "template_packs",
    )

    assert re.fullmatch(r"tpl_[0-9a-f]{32}", generated["pack_id"])
    assert generated["pack_name"] == "夏日模板"
    assert (generated["installed_dir"] / "template.json").is_file()
    assert not (generated["output_dir"] / "zhenxun_astr_config.generated.json").exists()

    registry = TemplateRegistry(editor_server.PLUGIN_ROOT, tmp_path / "data")
    folder_pack = registry.load(generated["installed_dir"])
    zip_pack = registry.load(generated["zip_path"])
    assert folder_pack.name == "夏日模板"
    assert folder_pack.settings["brand_name"] == "夏娜"
    assert folder_pack.settings["date_format"] == "%Y年%m月%d日"
    assert zip_pack.id == generated["pack_id"]
    assert "当前模板" in folder_pack.read_text("README.md")
    assert "切换签到模板" not in folder_pack.read_text("README.md")
    assert "无需合并或替换 AstrBot 插件配置" in folder_pack.read_text("README.md")
    assert zip_pack.read_text("sign_card.html") == folder_pack.read_text(
        "sign_card.html"
    )
