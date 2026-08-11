import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from zhenxun_astr.integrations import FavourIntegrationManager, FavourSnapshot


class FakeEvent:
    unified_msg_origin = "aiocqhttp:GroupMessage:10001"


class FakeDatabase:
    def __init__(self, favour: int = 149) -> None:
        self.record = SimpleNamespace(
            favour=favour,
            relationship="朋友",
            is_unique=False,
        )

    async def get_favour(self, user_id: str, session_id: str):
        assert user_id == "123456789"
        assert session_id == "aiocqhttp"
        return self.record


class FakeFavourUltra:
    is_global_favour = True
    min_favour_value = -200
    max_favour_value = 1000
    default_favour = 0
    allowed_sessions: list[str] = []
    blocked_sessions: list[str] = []
    favour_levels = [
        {"min": -200, "max": -151, "name": "极度厌恶"},
        {"min": -150, "max": -51, "name": "厌恶"},
        {"min": -50, "max": -1, "name": "反感"},
        {"min": 0, "max": 149, "name": "普通"},
        {"min": 150, "max": 299, "name": "喜欢"},
        {"min": 300, "max": 449, "name": "亲密"},
        {"min": 450, "max": 500, "name": "挚爱"},
    ]

    def __init__(self) -> None:
        self.db_manager = FakeDatabase()
        self.write_calls: list[dict] = []

    def _get_session_id(self, event: FakeEvent) -> str:
        return event.unified_msg_origin.split(":", 1)[0]

    @staticmethod
    def _is_shared_session(session_id: str) -> bool:
        return ":" not in session_id

    async def _get_initial_favour(self, event: FakeEvent) -> int:
        return self.default_favour

    async def _write_favour(
        self,
        user_id: str,
        session_id: str,
        favour: int | None = None,
        touch_interaction: bool = True,
    ) -> bool:
        self.write_calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "favour": favour,
                "touch_interaction": touch_interaction,
            }
        )
        if favour is not None:
            self.db_manager.record.favour = favour
        return True


class FakeContext:
    def __init__(self, instance: FakeFavourUltra | None) -> None:
        self.metadata = (
            SimpleNamespace(
                name="astrbot_plugin_Favour_Ultra",
                root_dir_name="astrbot_plugin_Favour_Ultra",
                activated=True,
                version="v4.3.0",
                star_cls=instance,
            )
            if instance is not None
            else None
        )

    def get_registered_star(self, name: str):
        if name == "astrbot_plugin_Favour_Ultra":
            return self.metadata
        return None

    def get_all_stars(self) -> list:
        return [self.metadata] if self.metadata is not None else []


def build_manager(
    instance: FakeFavourUltra | None,
    **overrides,
) -> FavourIntegrationManager:
    config = {
        "favour_integration": {
            "provider": "favour_ultra",
            "mode": "display_reward",
            "reward_mode": "fixed",
            "reward_value": 2,
            "touch_interaction": True,
            **overrides,
        }
    }
    return FavourIntegrationManager(
        FakeContext(instance),
        config,
        logging.getLogger("test-favour-integration"),
    )


@pytest.mark.asyncio
async def test_favour_ultra_global_snapshot_and_sign_reward() -> None:
    instance = FakeFavourUltra()
    manager = build_manager(instance)

    before = await manager.get_snapshot(FakeEvent(), "123456789")
    after = await manager.handle_sign(FakeEvent(), "123456789", True)

    assert before.available is True
    assert before.session_id == "aiocqhttp"
    assert before.value == 149
    assert before.level_name == "普通"
    assert before.next_required == 1
    before_values = manager.template_values(before)
    assert before_values["favour_level"] == "4 [熟悉]"
    assert before_values["favour_provider_level"] == "普通"
    assert before_values["favour_zhenxun_level"] == 4
    assert before_values["favour_zhenxun_relation"] == "熟悉"
    assert before_values["favour_attitude"] == "是个好人"
    assert manager.filled_hearts(before, 8) == 4
    assert after.value == 151
    assert after.reward_delta == 2
    assert after.level_name == "喜欢"
    assert after.next_required == 149
    after_values = manager.template_values(after)
    assert after_values["favour_level"] == "5 [信赖]"
    assert after_values["favour_attitude"] == "好朋友"
    assert instance.write_calls == [
        {
            "user_id": "123456789",
            "session_id": "aiocqhttp",
            "favour": 151,
            "touch_interaction": True,
        }
    ]


@pytest.mark.asyncio
async def test_repeated_sign_only_reads_and_external_change_invalidates_token() -> None:
    instance = FakeFavourUltra()
    manager = build_manager(instance)

    first = await manager.handle_sign(FakeEvent(), "123456789", False)
    instance.db_manager.record.favour = 200
    second = await manager.handle_sign(FakeEvent(), "123456789", False)

    assert instance.write_calls == []
    assert first.cache_token() != second.cache_token()
    assert second.value == 200


@pytest.mark.asyncio
async def test_display_only_and_missing_provider_fail_open() -> None:
    instance = FakeFavourUltra()
    display_only = build_manager(instance, mode="display_only")
    missing = build_manager(None)

    snapshot = await display_only.handle_sign(FakeEvent(), "123456789", True)
    unavailable = await missing.handle_sign(FakeEvent(), "123456789", True)

    assert snapshot.value == 149
    assert instance.write_calls == []
    assert unavailable.available is False
    assert unavailable.status == "unavailable"
    assert missing.template_values(unavailable)["favour_level"] == "不可用"


def test_schema_exposes_optional_favour_integration() -> None:
    schema_path = Path(__file__).parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    fields = schema["favour_integration"]["items"]

    assert fields["provider"]["options"] == ["none", "favour_ultra"]
    assert fields["provider"]["default"] == "none"
    assert fields["mode"]["options"] == ["display_reward", "display_only"]
    assert fields["attitude_source"]["options"] == [
        "zhenxun",
        "level",
        "relationship",
    ]
    assert fields["attitude_source"]["default"] == "zhenxun"


def test_attitude_sources_and_legacy_setting_remain_distinct() -> None:
    snapshot = FavourSnapshot(
        provider_id="favour_ultra",
        status="ready",
        available=True,
        value=88,
        relationship="朋友",
        level_name="普通",
        level_index=3,
        level_count=7,
        range_progress=24,
    )
    legacy = build_manager(None, attitude_source="relationship_or_level")
    level = build_manager(None, attitude_source="level")
    relationship = build_manager(None, attitude_source="relationship")

    assert legacy.settings.attitude_source == "zhenxun"
    assert legacy.template_values(snapshot)["favour_attitude"] == "是个好人"
    assert level.template_values(snapshot)["favour_attitude"] == "普通"
    assert relationship.template_values(snapshot)["favour_attitude"] == "朋友"
