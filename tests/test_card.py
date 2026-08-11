import asyncio
import json
import os
import time
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Reply
from PIL import Image
from zhenxun_astr.integrations import FavourSnapshot
from zhenxun_astr.main import (
    AVATAR_CACHE_TTL_SECONDS,
    CARD_HEIGHT,
    CARD_WIDTH,
    ZhenxunSign,
    _InfoCommandFilter,
    _resolve_render_backend,
    _SignCommandFilter,
)
from zhenxun_astr.storage import SignStore


class FakeEvent:
    session_id = "session"

    def __init__(self, original: str = "", processed: str | None = None) -> None:
        self.message_obj = SimpleNamespace(
            message_str=original,
            message_id=-1114368830,
        )
        self.message_str = original if processed is None else processed
        self.sent_messages: list[MessageChain | None] = []

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

    def image_result(self, path: str) -> str:
        return path

    async def send(self, message: MessageChain | None) -> None:
        self.sent_messages.append(message)


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
    assert "font-family: 'kcytFont', 'shFont', sans-serif;" in sign_style
    assert "font-family: 'rxxxtFont', 'shFont', sans-serif;" in sign_style
    assert assets["main_character"].startswith("data:image/png;base64,")
    assert assets["footer_character"].startswith("data:image/png;base64,")
    assert len(assets["tags"]) == 6
    assert len(assets["weather"]) == 12


def test_local_asset_bundle_uses_file_uris_and_small_html(tmp_path: Path) -> None:
    plugin = build_plugin()
    plugin.data_dir = tmp_path
    template_pack = plugin.template_registry.legacy_pack()
    card = plugin._build_card_data(
        event=FakeEvent(),
        record={
            "user_id": "123456789",
            "platform": "aiocqhttp",
            "uid": 1,
            "sign_count": 1,
            "last_sign_date": "2026-08-11",
            "gold_balance": 0,
            "items": {},
        },
        display_name="测试用户",
        current_time=datetime(
            2026,
            8,
            11,
            12,
            0,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        mode="sign",
        is_new_sign=True,
        reward={"gold": 0, "items": []},
        template_pack=template_pack,
    )
    card["user"]["avatar_source"] = ""

    async def prepare_and_render() -> tuple[dict, str]:
        render_data = await plugin._prepare_render_data(
            card,
            template_pack,
            asset_mode="local",
        )
        rendered_html = await plugin.local_renderer._render_template(
            template_pack.read_text(template_pack.template_file),
            render_data,
            template_pack.fingerprint,
        )
        return render_data, rendered_html

    render_data, rendered_html = asyncio.run(prepare_and_render())
    local_root = plugin._local_asset_root(template_pack)

    assert local_root.is_dir()
    assert plugin._local_base_document(template_pack).is_file()
    assert "data:font/woff2;base64," not in render_data["sign_style"]
    assert "file:///" in render_data["sign_style"]
    assert render_data["assets"]["main_character"].startswith("file:///")
    assert len(rendered_html.encode("utf-8")) < 64 * 1024


def test_template_fingerprint_invalidates_local_assets_and_card_cache(
    tmp_path: Path,
) -> None:
    plugin = build_plugin({"image_cache": {"enabled": True}})
    plugin.data_dir = tmp_path
    template_pack = plugin.template_registry.legacy_pack()
    changed_pack = replace(
        template_pack,
        fingerprint=f"{template_pack.fingerprint}:changed",
    )
    card = {
        "last_sign_date": "2026-08-11",
        "sign_count": 1,
        "gold_balance": 0,
        "inventory": {},
    }

    assert plugin._local_asset_root(template_pack) != plugin._local_asset_root(
        changed_pack
    )
    assert plugin._card_cache_path(
        "aiocqhttp::123456789",
        "2026-08-11",
        template_pack,
        card,
    ) != plugin._card_cache_path(
        "aiocqhttp::123456789",
        "2026-08-11",
        changed_pack,
        card,
    )


def test_local_asset_materialization_supports_zip_templates(tmp_path: Path) -> None:
    plugin = build_plugin()
    plugin.data_dir = tmp_path / "data"
    source_pack = plugin.template_registry.legacy_pack()
    archive_path = tmp_path / "template.zip"
    manifest = {
        "format": "zhenxun-astr-template",
        "version": 1,
        "id": "zip-local-test",
        "name": "ZIP 本地测试",
        "card": {"width": CARD_WIDTH, "height": CARD_HEIGHT},
        "files": {
            "template": source_pack.template_file,
            "style": source_pack.style_file,
            "assets": source_pack.asset_root,
        },
        "settings": {},
    }
    included_files = (
        source_pack.template_file,
        source_pack.style_file,
        *(
            name
            for name in source_pack.file_names
            if name.startswith(f"{source_pack.asset_root}/")
        ),
    )
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("template.json", json.dumps(manifest, ensure_ascii=False))
        for relative_path in included_files:
            archive.writestr(
                relative_path,
                source_pack.read_bytes(relative_path),
            )

    zip_pack = plugin.template_registry.load(archive_path)
    sign_style, assets = plugin._load_asset_bundle_sync(
        zip_pack,
        asset_mode="local",
    )

    assert zip_pack.source_kind == "zip"
    assert plugin._local_base_document(zip_pack).is_file()
    assert "data:font/woff2;base64," not in sign_style
    assert assets["main_character"].startswith("file:///")


def test_avatar_disk_cache_supports_local_and_remote_renderers(
    tmp_path: Path,
) -> None:
    plugin = build_plugin()
    plugin.data_dir = tmp_path
    source = "https://example.invalid/avatar.png"
    cache_path = plugin._avatar_cache_file(source)
    cache_path.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), "pink").save(cache_path, format="PNG")

    local_uri = asyncio.run(
        plugin._avatar_data_uri(source, "fallback", local_assets=True)
    )
    remote_uri = asyncio.run(
        plugin._avatar_data_uri(source, "fallback", local_assets=False)
    )

    assert local_uri == cache_path.resolve().as_uri()
    assert remote_uri.startswith("data:image/png;base64,")


def test_stale_avatar_cache_is_refreshed(tmp_path: Path) -> None:
    plugin = build_plugin()
    plugin.data_dir = tmp_path
    source = "https://example.invalid/avatar.png"
    cache_path = plugin._avatar_cache_file(source)
    cache_path.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), "pink").save(cache_path, format="PNG")
    stale_time = time.time() - AVATAR_CACHE_TTL_SECONDS - 1
    os.utime(cache_path, (stale_time, stale_time))
    refresh_calls: list[tuple[str, bool]] = []

    async def fake_download(value: str, local_assets: bool) -> str:
        refresh_calls.append((value, local_assets))
        return "refreshed-avatar"

    plugin._download_avatar_uri = fake_download

    result = asyncio.run(plugin._avatar_data_uri(source, "fallback", local_assets=True))

    assert result == "refreshed-avatar"
    assert refresh_calls == [(source, True)]


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
            "uid": 1,
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
    assert card["user"]["uid_str"] == "0000 0000 0001"
    assert card["reserved"]["current"] == "--"
    assert card["reserved"]["level_text"] == "未接入"
    assert card["reserved"]["progress"] == 0
    assert "排名" not in card["info"]["primary_text"]


def test_card_data_uses_live_favour_snapshot_and_legacy_placeholders() -> None:
    plugin = build_plugin(
        {
            "favour_integration": {
                "provider": "favour_ultra",
                "attitude_source": "relationship_or_level",
            }
        }
    )
    default_pack = plugin.template_registry.legacy_pack()
    legacy_settings = default_pack.settings | {
        "reserved_panel": {
            "current_label": "当前好感度",
            "current": "--",
            "level_label": "好感度等级",
            "level_text": "未接入",
            "attitude": "对你的态度: 未接入",
            "next_text": "距离升级还差--好感度",
            "separator": ": ",
            "heart_count": 8,
            "filled_hearts": 0,
            "progress": 0,
        }
    }
    custom_pack = replace(default_pack, settings=legacy_settings)
    snapshot = FavourSnapshot(
        provider_id="favour_ultra",
        provider_version="v4.3.0",
        status="ready",
        available=True,
        session_id="aiocqhttp",
        value=151,
        relationship="朋友",
        level_name="喜欢",
        level_min=150,
        level_max=299,
        level_index=4,
        level_count=7,
        level_progress=1 / 149 * 100,
        tier_progress=58,
        range_progress=29,
        next_level_name="亲密",
        next_required=149,
    )
    card = plugin._build_card_data(
        event=FakeEvent(),
        record={
            "user_id": "123456789",
            "platform": "aiocqhttp",
            "uid": 1,
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
        mode="view",
        is_new_sign=False,
        reward=None,
        template_pack=custom_pack,
        favour_snapshot=snapshot,
    )

    assert card["reserved"]["current"] == "151"
    assert card["reserved"]["level_text"] == "5 [信赖]"
    assert card["reserved"]["attitude"] == "对你的态度: 好朋友"
    assert card["reserved"]["next_text"] == "距离升级还差149好感度"
    assert card["reserved"]["progress"] == pytest.approx(1 / 149 * 100)
    assert len(card["reserved"]["heart2"]) > 0

    first_cache_path = plugin._card_cache_path(
        "aiocqhttp::123456789",
        "2026-08-10",
        custom_pack,
        card,
    )
    changed_cache_path = plugin._card_cache_path(
        "aiocqhttp::123456789",
        "2026-08-10",
        custom_pack,
        card | {"favour_cache_token": "changed"},
    )
    assert first_cache_path != changed_cache_path


def test_sign_flow_only_applies_favour_reward_to_new_sign(
    tmp_path: Path,
) -> None:
    plugin = build_plugin(
        {
            "favour_integration": {
                "provider": "favour_ultra",
                "mode": "display_reward",
                "reward_value": 1,
            }
        }
    )
    plugin.data_dir = tmp_path
    plugin.store = SignStore(tmp_path / "sign_data.json")
    calls: list[str] = []
    cards: list[dict] = []

    class RecordingProvider:
        async def add_favour(self, event, user_id, amount):
            calls.append(f"add:{user_id}:{amount}")
            return FavourSnapshot(
                provider_id="favour_ultra",
                status="ready",
                available=True,
                session_id="aiocqhttp",
                value=1,
                level_name="普通",
                level_min=0,
                level_max=149,
                level_count=1,
                next_required=149,
                reward_delta=1,
            )

        async def get_snapshot(self, event, user_id):
            calls.append(f"read:{user_id}")
            return FavourSnapshot(
                provider_id="favour_ultra",
                status="ready",
                available=True,
                session_id="aiocqhttp",
                value=1,
                level_name="普通",
                level_min=0,
                level_max=149,
                level_count=1,
                next_required=149,
            )

    async def fake_render(event, card_data, template_pack, **kwargs):
        cards.append(card_data)
        return "card.png"

    plugin.favour_integration.provider = RecordingProvider()
    plugin._render_or_text = fake_render

    async def sign_twice() -> list[str]:
        results = []
        async for result in plugin.sign(FakeEvent()):
            results.append(result)
        async for result in plugin.sign(FakeEvent()):
            results.append(result)
        return results

    results = asyncio.run(sign_twice())

    assert results == ["card.png", "card.png"]
    assert calls == ["add:123456789:1", "read:123456789"]
    assert cards[0]["favour"]["favour_delta"] == 1
    assert cards[1]["favour"]["favour_delta"] == 0


def test_uid_visibility_matches_original_toggle() -> None:
    hidden_plugin = build_plugin({"show_user_id": False})
    visible_plugin = build_plugin({"show_user_id": True})

    assert hidden_plugin._format_uid("987654321", 2) == "0000 0000 0002"
    assert visible_plugin._format_uid("987654321", 2) == "0009 8765 4321"


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


def test_render_backend_defaults_to_local_first_and_validates_values() -> None:
    assert _resolve_render_backend({}) == "auto"
    assert _resolve_render_backend({"render_backend": "local"}) == "local"
    assert _resolve_render_backend({"render_backend": "remote"}) == "remote"
    assert _resolve_render_backend({"render_backend": "unsupported"}) == "auto"


@pytest.mark.parametrize(
    ("mode", "initial_reply", "expected_reply"),
    [
        ("never", "global-reply", None),
        ("global", "global-reply", "global-reply"),
        ("always", "global-reply", "-1114368830"),
    ],
)
def test_reply_quote_mode_only_changes_this_plugin_send(
    mode: str,
    initial_reply: str,
    expected_reply: str | int | None,
) -> None:
    plugin = build_plugin({"reply_quote_mode": mode})
    event = FakeEvent()
    original_send = event.send
    message = MessageChain([Reply(id=initial_reply), Plain("签到卡片")])

    async def send() -> None:
        with plugin._reply_quote_scope(event, message):
            if mode == "never":
                message.chain.insert(0, Reply(id="decorator-reply"))
            await event.send(message)

    asyncio.run(send())

    assert event.send == original_send
    sent = event.sent_messages[0]
    assert isinstance(sent, MessageChain)
    replies = [component.id for component in sent.chain if isinstance(component, Reply)]
    assert replies == ([] if expected_reply is None else [expected_reply])
    assert any(
        isinstance(component, Plain) and component.text == "签到卡片"
        for component in sent.chain
    )


def test_reply_quote_mode_defaults_to_never_and_rejects_unknown_values() -> None:
    assert build_plugin().reply_quote_mode == "never"
    assert build_plugin({"reply_quote_mode": "global"}).reply_quote_mode == "global"
    assert build_plugin({"reply_quote_mode": "always"}).reply_quote_mode == "always"
    assert build_plugin({"reply_quote_mode": "invalid"}).reply_quote_mode == "never"


def test_local_renderer_is_preferred_when_available() -> None:
    plugin = build_plugin()

    class FakeRenderer:
        async def start(self) -> bool:
            return True

        async def render(self, *_args, **_kwargs) -> str:
            return "local.png"

    async def unexpected_remote(*_args, **_kwargs):
        raise AssertionError("remote renderer should not be called")

    async def fake_prepare(*_args, **kwargs) -> dict:
        assert kwargs["asset_mode"] == "local"
        return {}

    plugin.local_renderer = FakeRenderer()
    plugin.html_render = unexpected_remote
    plugin._prepare_render_data = fake_prepare
    template_pack = plugin.template_registry.legacy_pack()

    image_path, backend, prepare_ms, render_ms = asyncio.run(
        plugin._render_template_image("<div></div>", {}, template_pack)
    )

    assert image_path == "local.png"
    assert backend == "local"
    assert prepare_ms >= 0
    assert render_ms >= 0


def test_remote_renderer_keeps_self_contained_base64_assets() -> None:
    plugin = build_plugin({"render_backend": "remote"})
    captured: dict = {}

    async def fake_remote(_template: str, render_data: dict, **_kwargs) -> str:
        captured.update(render_data)
        return "remote.png"

    plugin.html_render = fake_remote
    template_pack = plugin.template_registry.legacy_pack()
    card = {
        "page": {"tag_icon_name": "0.png", "weather_icon_name": "0.png"},
        "user": {"avatar_source": ""},
    }

    image_path, backend, prepare_ms, render_ms = asyncio.run(
        plugin._render_template_image("<div></div>", card, template_pack)
    )

    assert image_path == "remote.png"
    assert backend == "remote"
    assert "data:font/woff2;base64," in captured["sign_style"]
    assert captured["assets"]["main_character"].startswith("data:image/png;base64,")
    assert prepare_ms >= 0
    assert render_ms >= 0


def test_daily_card_cache_reuses_first_image_and_prunes_old_day(
    tmp_path: Path,
) -> None:
    plugin = build_plugin({"image_cache": {"enabled": True}})
    plugin.data_dir = tmp_path
    template_pack = plugin.template_registry.legacy_pack()
    render_calls: list[Path] = []

    async def fake_prepare(*_args, **_kwargs) -> dict:
        return {}

    async def fake_render(*_args, **_kwargs) -> tuple[str, str, float, float]:
        image_path = tmp_path / f"render-{len(render_calls)}.png"
        Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "white").save(
            image_path,
            format="PNG",
        )
        render_calls.append(image_path)
        return str(image_path), "local", 0.0, 0.0

    plugin._prepare_render_data = fake_prepare
    plugin._render_template_image = fake_render
    event = FakeEvent()
    unsigned_card = {
        "last_sign_date": "还没有记录",
        "sign_count": 0,
        "gold_balance": 0,
        "inventory": {},
    }
    signed_card = unsigned_card | {
        "last_sign_date": "2026-08-11",
        "sign_count": 1,
    }

    async def render_twice() -> tuple[str, str]:
        return await asyncio.gather(
            plugin._render_card(
                event,
                unsigned_card,
                template_pack,
                cache_identity="aiocqhttp::123456789",
                cache_date="2026-08-11",
            ),
            plugin._render_card(
                event,
                unsigned_card,
                template_pack,
                cache_identity="aiocqhttp::123456789",
                cache_date="2026-08-11",
            ),
        )

    first_path, repeated_path = asyncio.run(render_twice())

    assert first_path == repeated_path
    assert Path(first_path).is_file()
    assert len(render_calls) == 1

    signed_path = asyncio.run(
        plugin._render_card(
            event,
            signed_card,
            template_pack,
            cache_identity="aiocqhttp::123456789",
            cache_date="2026-08-11",
        )
    )

    assert signed_path != first_path
    assert Path(signed_path).is_file()
    assert len(render_calls) == 2

    next_day_path = asyncio.run(
        plugin._render_card(
            event,
            signed_card,
            template_pack,
            cache_identity="aiocqhttp::123456789",
            cache_date="2026-08-12",
        )
    )

    assert next_day_path != first_path
    assert Path(next_day_path).is_file()
    assert not Path(first_path).exists()
    assert not Path(signed_path).exists()
    assert len(render_calls) == 3


def test_daily_card_cache_can_be_disabled() -> None:
    plugin = build_plugin({"image_cache": {"enabled": False}})
    template_pack = plugin.template_registry.legacy_pack()

    assert (
        plugin._card_cache_path(
            "aiocqhttp::123456789",
            "2026-08-11",
            template_pack,
        )
        is None
    )
