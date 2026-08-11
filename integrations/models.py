from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class FavourIntegrationSettings:
    """Runtime settings shared by optional favour providers."""

    provider: str = "none"
    mode: str = "display_reward"
    reward_mode: str = "fixed"
    reward_value: int = 1
    reward_min: int = 1
    reward_max: int = 3
    touch_interaction: bool = True
    heart_mode: str = "level"
    attitude_source: str = "relationship_or_level"

    @classmethod
    def from_plugin_config(
        cls, plugin_config: dict[str, Any]
    ) -> "FavourIntegrationSettings":
        configured = plugin_config.get("favour_integration", {})
        if not isinstance(configured, dict):
            configured = {}

        provider = str(configured.get("provider", "none") or "none").lower()
        if provider not in {"none", "favour_ultra"}:
            provider = "none"

        mode = str(configured.get("mode", "display_reward") or "display_reward")
        if mode not in {"display_only", "display_reward"}:
            mode = "display_reward"

        reward_mode = str(configured.get("reward_mode", "fixed") or "fixed")
        if reward_mode not in {"fixed", "random"}:
            reward_mode = "fixed"

        reward_value = cls._non_negative_int(configured.get("reward_value", 1), 1)
        reward_min = cls._non_negative_int(configured.get("reward_min", 1), 1)
        reward_max = cls._non_negative_int(configured.get("reward_max", 3), 3)
        if reward_max < reward_min:
            reward_min, reward_max = reward_max, reward_min

        heart_mode = str(configured.get("heart_mode", "level") or "level")
        if heart_mode not in {"level", "range"}:
            heart_mode = "level"

        attitude_source = str(
            configured.get("attitude_source", "relationship_or_level")
            or "relationship_or_level"
        )
        if attitude_source not in {
            "relationship_or_level",
            "relationship",
            "level",
        }:
            attitude_source = "relationship_or_level"

        return cls(
            provider=provider,
            mode=mode,
            reward_mode=reward_mode,
            reward_value=reward_value,
            reward_min=reward_min,
            reward_max=reward_max,
            touch_interaction=bool(configured.get("touch_interaction", True)),
            heart_mode=heart_mode,
            attitude_source=attitude_source,
        )

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    @property
    def reward_enabled(self) -> bool:
        return self.enabled and self.mode == "display_reward"

    def next_reward(self) -> int:
        if not self.reward_enabled:
            return 0
        if self.reward_mode == "random":
            return random.randint(self.reward_min, self.reward_max)
        return self.reward_value

    @staticmethod
    def _non_negative_int(value: Any, default: int) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default


@dataclass(frozen=True, slots=True)
class FavourSnapshot:
    """Provider-neutral favour data used by card rendering and cache keys."""

    provider_id: str = "none"
    provider_version: str = ""
    status: str = "disabled"
    available: bool = False
    session_id: str = ""
    value: int | None = None
    relationship: str = ""
    is_unique: bool = False
    level_name: str = ""
    level_min: int | None = None
    level_max: int | None = None
    level_index: int = 0
    level_count: int = 0
    level_progress: float = 0.0
    tier_progress: float = 0.0
    range_progress: float = 0.0
    next_level_name: str = ""
    next_required: int | None = None
    is_max_level: bool = False
    reward_delta: int = 0
    error: str = ""

    @classmethod
    def unavailable(
        cls,
        provider_id: str,
        status: str,
        error: str = "",
    ) -> "FavourSnapshot":
        return cls(
            provider_id=provider_id,
            status=status,
            error=error,
        )

    def cache_token(self) -> str:
        payload = {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "status": self.status,
            "available": self.available,
            "session_id": self.session_id,
            "value": self.value,
            "relationship": self.relationship,
            "is_unique": self.is_unique,
            "level_name": self.level_name,
            "level_min": self.level_min,
            "level_max": self.level_max,
            "level_index": self.level_index,
            "level_count": self.level_count,
            "level_progress": round(self.level_progress, 4),
            "tier_progress": round(self.tier_progress, 4),
            "range_progress": round(self.range_progress, 4),
            "next_level_name": self.next_level_name,
            "next_required": self.next_required,
            "is_max_level": self.is_max_level,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class FavourProvider(Protocol):
    async def get_snapshot(self, event: Any, user_id: str) -> FavourSnapshot: ...

    async def add_favour(
        self,
        event: Any,
        user_id: str,
        amount: int,
    ) -> FavourSnapshot: ...


class DisabledFavourProvider:
    async def get_snapshot(self, event: Any, user_id: str) -> FavourSnapshot:
        return FavourSnapshot()

    async def add_favour(
        self,
        event: Any,
        user_id: str,
        amount: int,
    ) -> FavourSnapshot:
        return FavourSnapshot()
