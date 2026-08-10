from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from PIL import Image
from zhenxun_astr.main import (
    CARD_HEIGHT,
    CARD_WIDTH,
    ZhenxunSign,
    _InfoCommandFilter,
    _SignCommandFilter,
)


class FakeEvent:
    session_id = "session"

    def __init__(self, original: str = "", processed: str | None = None) -> None:
        self.message_obj = SimpleNamespace(message_str=original)
        self.message_str = original if processed is None else processed

    def get_message_str(self) -> str:
        return self.message_str

    def get_sender_id(self) -> str:
        return "123456789"

    def get_platform_id(self) -> str:
        return "aiocqhttp"

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_sender_name(self) -> str:
        return "测试用户"

    def plain_result(self, text: str) -> str:
        return text


def build_plugin(config: dict | None = None) -> ZhenxunSign:
    return ZhenxunSign(object(), config or {})


def test_template_uses_original_zhenxun_structure() -> None:
    plugin = build_plugin()
    template = plugin.template_path.read_text(encoding="utf-8")
    style = plugin.style_path.read_text(encoding="utf-8")

    assert 'class="wrapper"' in template
    assert 'class="sign-content"' in template
    assert 'class="bottom-foot"' in template
    assert 'class="card' not in template
    assert ".wrapper{" in style
    assert "height: 926px;" in style
    assert "width: 465px;" in style


def test_asset_bundle_contains_original_fonts_and_images() -> None:
    sign_style, assets = build_plugin()._load_asset_bundle_sync()

    assert sign_style.count("data:font/woff2;base64,") == 5
    assert sign_style.count("@font-face") == 5
    assert assets["main_character"].startswith("data:image/png;base64,")
    assert assets["footer_character"].startswith("data:image/png;base64,")
    assert len(assets["tags"]) == 6
    assert len(assets["weather"]) == 12


def test_card_data_keeps_favourability_as_placeholder() -> None:
    plugin = build_plugin({"brand_name": "旧全局名称", "show_user_id": False})
    default_pack = plugin.template_registry.legacy_pack()
    custom_pack = replace(
        default_pack,
        settings=default_pack.settings | {"brand_name": "模板名称"},
    )
    card = plugin._build_card_data(
        event=FakeEvent(),
        record={
            "user_id": "123456789",
            "platform": "aiocqhttp",
            "sign_count": 3,
            "last_sign_date": "2026-08-10",
            "gold_balance": 0,
            "items": {},
        },
        display_name="测试用户",
        current_time=datetime(
            2026,
            8,
            10,
            12,
            34,
            56,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        mode="sign",
        is_new_sign=True,
        reward={"gold": 0, "items": []},
        template_pack=custom_pack,
    )

    assert card["bot_message"] == "模板名称说: 模板名称希望你开心！"
    assert "旧全局名称" not in card["bot_message"]
    assert card["user"]["uid_str"] == "XXXX XXXX XXXX"
    assert card["reserved"]["current"] == "--"
    assert card["reserved"]["level_text"] == "未接入"
    assert card["reserved"]["progress"] == 0
    assert "排名" not in card["info"]["primary_text"]


def test_crop_card_outputs_exact_png(tmp_path: Path) -> None:
    source_path = tmp_path / "rendered.jpg"
    Image.new("RGB", (800, CARD_HEIGHT), "white").save(source_path, format="JPEG")

    result_path = Path(ZhenxunSign._crop_card(str(source_path)))

    assert result_path.suffix == ".png"
    assert not source_path.exists()
    with Image.open(result_path) as result:
        assert result.size == (CARD_WIDTH, CARD_HEIGHT)
        assert result.format == "PNG"


def test_active_template_is_selected_from_plugin_config() -> None:
    plugin = build_plugin({"template_management": {"active_template": "tpl_random"}})
    default_pack = plugin.template_registry.legacy_pack()
    custom_pack = replace(
        default_pack,
        id="tpl_random",
        legacy_ids=("shana",),
        name="夏娜",
    )
    packs = {default_pack.id: default_pack, custom_pack.id: custom_pack}

    assert plugin._active_template(packs) is custom_pack
    assert plugin._selected_template_id() == "tpl_random"


def test_legacy_template_selection_migrates_into_config(tmp_path: Path) -> None:
    plugin = build_plugin({"template_management": {"active_template": "default"}})
    plugin.data_dir = tmp_path
    legacy_path = tmp_path / "active_template.txt"
    legacy_path.write_text("shana\n", encoding="utf-8")

    plugin._migrate_legacy_template_selection()

    assert plugin._selected_template_id() == "shana"
    assert not legacy_path.exists()


def test_command_prefix_modes_match_original_message_exactly() -> None:
    cases = [
        ({"command_prefix_mode": "slash"}, "/签到", "签到", True),
        ({"command_prefix_mode": "slash"}, "签到", "签到", False),
        ({"command_prefix_mode": "none"}, "签到", "签到", True),
        ({"command_prefix_mode": "none"}, "/签到", "签到", False),
        ({"command_prefix_mode": "hash"}, "#打卡", "#打卡", True),
        (
            {"command_prefix_mode": "custom", "custom_command_prefix": "!!"},
            "!!签到",
            "!!签到",
            True,
        ),
    ]

    for config, original, processed, expected in cases:
        build_plugin(config)
        event = FakeEvent(original, processed)
        assert _SignCommandFilter().filter(event, {}) is expected


def test_command_prefix_also_applies_to_sign_status() -> None:
    build_plugin({"command_prefix_mode": "hash"})

    assert _InfoCommandFilter().filter(FakeEvent("#我的签到"), {}) is True
    assert _InfoCommandFilter().filter(FakeEvent("我的签到"), {}) is False
