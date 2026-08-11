from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from typing import Any

from .models import FavourIntegrationSettings, FavourSnapshot

PROVIDER_ID = "favour_ultra"
PLUGIN_NAME = "astrbot_plugin_Favour_Ultra"
LOOKUP_TIMEOUT_SECONDS = 3.0


class FavourUltraProvider:
    """Optional adapter for a live astrbot_plugin_Favour_Ultra instance."""

    def __init__(
        self,
        context: Any,
        settings: FavourIntegrationSettings,
        logger: Any,
    ) -> None:
        self.context = context
        self.settings = settings
        self.logger = logger
        self._last_notice = ""

    async def get_snapshot(self, event: Any, user_id: str) -> FavourSnapshot:
        metadata, instance, error = self._resolve_instance()
        if instance is None:
            self._warn_once(error)
            return FavourSnapshot.unavailable(PROVIDER_ID, "unavailable", error)

        try:
            snapshot = await asyncio.wait_for(
                self._read_snapshot(metadata, instance, event, user_id),
                timeout=LOOKUP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            error = "Favour Ultra read timed out"
            self._warn_once(error)
            return FavourSnapshot.unavailable(PROVIDER_ID, "timeout", error)
        except Exception as exception:
            error = f"Favour Ultra read failed: {exception}"
            self._warn_once(error)
            return FavourSnapshot.unavailable(PROVIDER_ID, "error", error)

        self._ready_once(snapshot.provider_version)
        return snapshot

    async def add_favour(
        self,
        event: Any,
        user_id: str,
        amount: int,
    ) -> FavourSnapshot:
        metadata, instance, error = self._resolve_instance()
        if instance is None:
            self._warn_once(error)
            return FavourSnapshot.unavailable(PROVIDER_ID, "unavailable", error)

        try:
            return await asyncio.wait_for(
                self._add_favour(metadata, instance, event, user_id, max(0, amount)),
                timeout=LOOKUP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            error = "Favour Ultra update timed out"
        except Exception as exception:
            error = f"Favour Ultra update failed: {exception}"

        self._warn_once(error)
        snapshot = await self.get_snapshot(event, user_id)
        return replace(snapshot, error=error)

    def _resolve_instance(self) -> tuple[Any | None, Any | None, str]:
        get_registered = getattr(self.context, "get_registered_star", None)
        metadata = None
        if callable(get_registered):
            metadata = get_registered(PLUGIN_NAME)

        if metadata is None:
            get_all = getattr(self.context, "get_all_stars", None)
            if callable(get_all):
                target = PLUGIN_NAME.casefold()
                for candidate in get_all() or []:
                    names = {
                        str(getattr(candidate, "name", "") or "").casefold(),
                        str(getattr(candidate, "root_dir_name", "") or "").casefold(),
                    }
                    if target in names:
                        metadata = candidate
                        break

        if metadata is None:
            return None, None, "Favour Ultra is not installed"
        if not bool(getattr(metadata, "activated", True)):
            return metadata, None, "Favour Ultra is disabled"

        instance = getattr(metadata, "star_cls", None)
        if instance is None:
            return metadata, None, "Favour Ultra is not ready"

        db_manager = getattr(instance, "db_manager", None)
        if not callable(getattr(db_manager, "get_favour", None)):
            return metadata, None, "Favour Ultra read capability is unavailable"
        if not callable(getattr(instance, "_get_session_id", None)):
            return metadata, None, "Favour Ultra session capability is unavailable"
        return metadata, instance, ""

    async def _read_snapshot(
        self,
        metadata: Any,
        instance: Any,
        event: Any,
        user_id: str,
    ) -> FavourSnapshot:
        session_id = str(instance._get_session_id(event) or "global")
        if not self._session_allowed(instance, session_id):
            return FavourSnapshot.unavailable(
                PROVIDER_ID,
                "blocked",
                f"Favour Ultra is disabled for session {session_id}",
            )

        record = await instance.db_manager.get_favour(str(user_id), session_id)
        if record is None:
            initial_getter = getattr(instance, "_get_initial_favour", None)
            if callable(initial_getter):
                value = self._as_int(await initial_getter(event), 0)
            else:
                value = self._as_int(getattr(instance, "default_favour", 0), 0)
            relationship = ""
            is_unique = False
        else:
            value = self._as_int(getattr(record, "favour", 0), 0)
            relationship = str(getattr(record, "relationship", "") or "")
            is_unique = bool(getattr(record, "is_unique", False))

        minimum = self._as_int(getattr(instance, "min_favour_value", -100), -100)
        maximum = self._as_int(getattr(instance, "max_favour_value", 100), 100)
        if maximum <= minimum:
            minimum, maximum = -100, 100
        value = min(maximum, max(minimum, value))

        level_data = self._level_data(
            value,
            getattr(instance, "favour_levels", []),
            minimum,
            maximum,
        )
        return FavourSnapshot(
            provider_id=PROVIDER_ID,
            provider_version=str(getattr(metadata, "version", "") or ""),
            status="ready",
            available=True,
            session_id=session_id,
            value=value,
            relationship=relationship,
            is_unique=is_unique,
            **level_data,
        )

    async def _add_favour(
        self,
        metadata: Any,
        instance: Any,
        event: Any,
        user_id: str,
        amount: int,
    ) -> FavourSnapshot:
        before = await self._read_snapshot(metadata, instance, event, user_id)
        if not before.available or before.value is None or amount <= 0:
            return before

        writer = getattr(instance, "_write_favour", None)
        if not callable(writer):
            error = "Favour Ultra write capability is unavailable"
            self._warn_once(error)
            return replace(before, error=error)

        maximum = self._as_int(getattr(instance, "max_favour_value", 100), 100)
        target_value = min(maximum, before.value + amount)
        if target_value == before.value:
            return before

        writer_kwargs: dict[str, Any] = {"favour": target_value}
        try:
            parameters = inspect.signature(writer).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "touch_interaction" in parameters:
            writer_kwargs["touch_interaction"] = self.settings.touch_interaction

        result = await writer(
            str(user_id),
            before.session_id,
            **writer_kwargs,
        )
        if result is False:
            error = "Favour Ultra rejected the sign reward"
            self._warn_once(error)
            return replace(before, error=error)

        after = await self._read_snapshot(metadata, instance, event, user_id)
        actual_delta = (
            max(0, after.value - before.value)
            if after.available and after.value is not None
            else 0
        )
        self._ready_once(after.provider_version)
        return replace(after, reward_delta=actual_delta)

    @staticmethod
    def _session_allowed(instance: Any, session_id: str) -> bool:
        shared_checker = getattr(instance, "_is_shared_session", None)
        if callable(shared_checker) and shared_checker(session_id):
            return True
        allowed = getattr(instance, "allowed_sessions", []) or []
        blocked = getattr(instance, "blocked_sessions", []) or []
        if allowed and session_id not in allowed:
            return False
        return session_id not in blocked

    @classmethod
    def _level_data(
        cls,
        value: int,
        raw_levels: Any,
        minimum: int,
        maximum: int,
    ) -> dict[str, Any]:
        range_progress = cls._percentage(value, minimum, maximum)
        levels: list[dict[str, Any]] = []
        if isinstance(raw_levels, list):
            for index, raw_level in enumerate(raw_levels):
                if not isinstance(raw_level, dict):
                    continue
                level_min = cls._as_int(raw_level.get("min"), minimum)
                level_max = cls._as_int(raw_level.get("max"), maximum)
                if level_max < level_min:
                    level_min, level_max = level_max, level_min
                levels.append(
                    {
                        "min": level_min,
                        "max": level_max,
                        "name": str(raw_level.get("name") or f"等级{index + 1}"),
                    }
                )
        levels.sort(key=lambda level: (level["min"], level["max"]))

        if not levels:
            return {
                "level_name": "未分级",
                "level_min": minimum,
                "level_max": maximum,
                "level_index": 0,
                "level_count": 1,
                "level_progress": range_progress,
                "tier_progress": range_progress,
                "range_progress": range_progress,
                "next_level_name": "上限" if value < maximum else "",
                "next_required": max(0, maximum - value) if value < maximum else None,
                "is_max_level": value >= maximum,
            }

        current_index = 0
        for index, level in enumerate(levels):
            if level["min"] <= value <= level["max"]:
                current_index = index
                break
            if value > level["max"]:
                current_index = index
            elif value < level["min"]:
                break

        current = levels[current_index]
        level_progress = cls._percentage(value, current["min"], current["max"])
        tier_progress = min(
            100.0,
            max(
                0.0,
                ((current_index + level_progress / 100.0) / len(levels)) * 100.0,
            ),
        )

        next_level = next(
            (level for level in levels if level["min"] > value),
            None,
        )
        return {
            "level_name": current["name"],
            "level_min": current["min"],
            "level_max": current["max"],
            "level_index": current_index,
            "level_count": len(levels),
            "level_progress": level_progress,
            "tier_progress": tier_progress,
            "range_progress": range_progress,
            "next_level_name": next_level["name"] if next_level else "",
            "next_required": max(0, next_level["min"] - value) if next_level else None,
            "is_max_level": next_level is None,
        }

    @staticmethod
    def _percentage(value: int, minimum: int, maximum: int) -> float:
        if maximum <= minimum:
            return 100.0 if value >= maximum else 0.0
        return min(100.0, max(0.0, ((value - minimum) / (maximum - minimum)) * 100))

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _warn_once(self, message: str) -> None:
        if message and message != self._last_notice:
            self.logger.warning("Favour integration unavailable: %s", message)
            self._last_notice = message

    def _ready_once(self, version: str) -> None:
        notice = f"ready:{version}"
        if notice != self._last_notice:
            self.logger.info(
                "Favour Ultra integration is ready%s",
                f" ({version})" if version else "",
            )
            self._last_notice = notice
