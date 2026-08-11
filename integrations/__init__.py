from __future__ import annotations

from typing import Any

from .favour_ultra import FavourUltraProvider
from .models import (
    DisabledFavourProvider,
    FavourIntegrationSettings,
    FavourProvider,
    FavourSnapshot,
)

ZHENXUN_RELATIONS = (
    "路人",
    "陌生",
    "初识",
    "普通",
    "熟悉",
    "信赖",
    "相知",
    "厚谊",
    "亲密",
)
ZHENXUN_ATTITUDES = (
    "排斥",
    "警惕",
    "可以交流",
    "一般",
    "是个好人",
    "好朋友",
    "可以分享小秘密",
    "喜欢",
    "恋人",
)


class FavourIntegrationManager:
    """Select an optional provider and expose provider-neutral card data."""

    def __init__(
        self, context: Any, plugin_config: dict[str, Any], logger: Any
    ) -> None:
        self.settings = FavourIntegrationSettings.from_plugin_config(plugin_config)
        self.provider: FavourProvider
        if self.settings.provider == "favour_ultra":
            self.provider = FavourUltraProvider(context, self.settings, logger)
        else:
            self.provider = DisabledFavourProvider()

    async def handle_sign(
        self,
        event: Any,
        user_id: str,
        is_new_sign: bool,
    ) -> FavourSnapshot:
        if is_new_sign and self.settings.reward_enabled:
            return await self.provider.add_favour(
                event,
                user_id,
                self.settings.next_reward(),
            )
        return await self.provider.get_snapshot(event, user_id)

    async def get_snapshot(self, event: Any, user_id: str) -> FavourSnapshot:
        return await self.provider.get_snapshot(event, user_id)

    def empty_snapshot(self) -> FavourSnapshot:
        if self.settings.enabled:
            return FavourSnapshot.unavailable(
                self.settings.provider,
                "unavailable",
            )
        return FavourSnapshot()

    def template_values(self, snapshot: FavourSnapshot) -> dict[str, Any]:
        if not snapshot.available or snapshot.value is None:
            unavailable_text = "不可用" if self.settings.enabled else "未接入"
            return {
                "favour": "--",
                "favour_level": unavailable_text,
                "favour_provider_level": unavailable_text,
                "favour_zhenxun_level": "--",
                "favour_zhenxun_relation": unavailable_text,
                "favour_level_min": "--",
                "favour_level_max": "--",
                "favour_relationship": "无",
                "favour_attitude": unavailable_text,
                "favour_next": "--",
                "favour_next_level": "",
                "favour_progress": 0,
                "favour_delta": 0,
                "favour_status": snapshot.status,
                "favour_available": False,
                "favour_is_max": False,
            }

        relationship = snapshot.relationship or "无"
        zhenxun_level = self._zhenxun_level(snapshot)
        zhenxun_relation = ZHENXUN_RELATIONS[zhenxun_level]
        if self.settings.attitude_source == "relationship":
            attitude = relationship
        elif self.settings.attitude_source == "level":
            attitude = snapshot.level_name
        else:
            attitude = ZHENXUN_ATTITUDES[zhenxun_level]

        return {
            "favour": snapshot.value,
            "favour_level": f"{zhenxun_level} [{zhenxun_relation}]",
            "favour_provider_level": snapshot.level_name,
            "favour_zhenxun_level": zhenxun_level,
            "favour_zhenxun_relation": zhenxun_relation,
            "favour_level_min": snapshot.level_min,
            "favour_level_max": snapshot.level_max,
            "favour_relationship": relationship,
            "favour_attitude": attitude,
            "favour_next": snapshot.next_required
            if snapshot.next_required is not None
            else 0,
            "favour_next_level": snapshot.next_level_name,
            "favour_progress": round(snapshot.level_progress, 2),
            "favour_delta": snapshot.reward_delta,
            "favour_status": snapshot.status,
            "favour_available": True,
            "favour_is_max": snapshot.is_max_level,
        }

    def filled_hearts(self, snapshot: FavourSnapshot, heart_count: int) -> int:
        if not snapshot.available or heart_count <= 0:
            return 0
        if self.settings.heart_mode == "range":
            return min(
                heart_count,
                max(0, round((snapshot.range_progress / 100.0) * heart_count)),
            )
        level = self._zhenxun_level(snapshot)
        return min(
            heart_count,
            max(0, round((level / (len(ZHENXUN_RELATIONS) - 1)) * heart_count)),
        )

    @staticmethod
    def _zhenxun_level(snapshot: FavourSnapshot) -> int:
        maximum_level = len(ZHENXUN_RELATIONS) - 1
        if snapshot.level_count > 1:
            ratio = snapshot.level_index / (snapshot.level_count - 1)
        else:
            ratio = snapshot.range_progress / 100.0
        return min(maximum_level, max(0, int(ratio * maximum_level + 0.5)))

    def cache_token(self, snapshot: FavourSnapshot) -> str:
        return "\0".join(
            (
                snapshot.cache_token(),
                self.settings.provider,
                self.settings.attitude_source,
                self.settings.heart_mode,
            )
        )


__all__ = [
    "FavourIntegrationManager",
    "FavourIntegrationSettings",
    "FavourSnapshot",
]
