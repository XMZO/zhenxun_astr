from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class SignStore:
    """Persist sign-in records without a fixed calendar range."""

    schema_version = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def get_record(
        self,
        key: str,
        user_id: str,
        platform: str,
        display_name: str,
    ) -> dict[str, Any]:
        """Return a user's record, creating an in-memory default when absent.

        Args:
            key: Stable storage key for the platform and user.
            user_id: Platform user identifier.
            platform: Platform or adapter identifier.
            display_name: Most recently observed display name.

        Returns:
            A normalized sign-in record.
        """
        async with self._lock:
            data = await asyncio.to_thread(self._read_sync)
            raw_record = data["users"].get(key)
            return deepcopy(
                self._normalize_record(
                    raw_record,
                    user_id=user_id,
                    platform=platform,
                    display_name=display_name,
                )
            )

    async def sign(
        self,
        key: str,
        user_id: str,
        platform: str,
        display_name: str,
        today: str,
        signed_at: str,
        reward: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Record one sign-in if the user has not signed in today.

        Args:
            key: Stable storage key for the platform and user.
            user_id: Platform user identifier.
            platform: Platform or adapter identifier.
            display_name: Most recently observed display name.
            today: Local calendar date in ISO format.
            signed_at: Sign-in timestamp in ISO format.
            reward: Reward payload reserved for the wallet and item modules.

        Returns:
            A tuple containing the normalized record and whether a new sign-in
            was recorded.
        """
        normalized_today = self.normalize_date(today) or today
        normalized_reward = self._normalize_reward(reward)

        async with self._lock:
            data = await asyncio.to_thread(self._read_sync)
            raw_record = data["users"].get(key)
            record = self._normalize_record(
                raw_record,
                user_id=user_id,
                platform=platform,
                display_name=display_name,
            )

            record["display_name"] = display_name or record["display_name"]
            if record["last_sign_date"] == normalized_today:
                return deepcopy(record), False

            record["sign_count"] += 1
            record["last_sign_date"] = normalized_today
            record["last_sign_at"] = signed_at
            record["gold_balance"] += normalized_reward["gold"]

            for item_name in normalized_reward["items"]:
                record["items"][item_name] = record["items"].get(item_name, 0) + 1

            record["last_reward"] = normalized_reward
            record["history"].append(
                {
                    "date": normalized_today,
                    "at": signed_at,
                    "gold": normalized_reward["gold"],
                    "items": normalized_reward["items"],
                }
            )
            data["schema_version"] = self.schema_version
            data["updated_at"] = signed_at
            data["users"][key] = record
            await asyncio.to_thread(self._write_sync, data)
            return deepcopy(record), True

    @classmethod
    def normalize_date(cls, value: Any) -> str:
        """Normalize common legacy date values without imposing a year limit.

        Args:
            value: An ISO date, datetime string, or date-like value.

        Returns:
            An ISO date string, or an empty string when the value is invalid.
        """
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if not value:
            return ""

        text = str(value).strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass

        try:
            return (
                datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
            )
        except ValueError:
            pass

        for date_format in ("%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, date_format).date().isoformat()
            except ValueError:
                continue
        return ""

    @classmethod
    def _empty_data(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.schema_version,
            "updated_at": "",
            "users": {},
        }

    @classmethod
    def _new_record(
        cls,
        user_id: str,
        platform: str,
        display_name: str,
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "platform": platform,
            "display_name": display_name,
            "sign_count": 0,
            "last_sign_date": "",
            "last_sign_at": "",
            "gold_balance": 0,
            "items": {},
            "last_reward": {"gold": 0, "items": []},
            "history": [],
            "extensions": {},
        }

    @classmethod
    def _normalize_record(
        cls,
        raw_record: Any,
        user_id: str,
        platform: str,
        display_name: str,
    ) -> dict[str, Any]:
        record = cls._new_record(user_id, platform, display_name)
        if isinstance(raw_record, dict):
            record.update(raw_record)

        record["user_id"] = str(record.get("user_id") or user_id)
        record["platform"] = str(record.get("platform") or platform)
        record["display_name"] = str(record.get("display_name") or display_name)
        record["sign_count"] = cls._non_negative_int(record.get("sign_count"))
        record["last_sign_date"] = cls.normalize_date(record.get("last_sign_date"))
        record["last_sign_at"] = str(record.get("last_sign_at") or "")
        record["gold_balance"] = cls._non_negative_int(record.get("gold_balance"))

        raw_items = record.get("items")
        if isinstance(raw_items, dict):
            record["items"] = {
                str(item_name): cls._non_negative_int(item_count)
                for item_name, item_count in raw_items.items()
                if cls._non_negative_int(item_count) > 0
            }
        else:
            record["items"] = {}

        record["last_reward"] = cls._normalize_reward(record.get("last_reward"))
        if not isinstance(record.get("history"), list):
            record["history"] = []
        if not isinstance(record.get("extensions"), dict):
            record["extensions"] = {}
        return record

    @classmethod
    def _normalize_reward(cls, reward: Any) -> dict[str, Any]:
        if not isinstance(reward, dict):
            return {"gold": 0, "items": []}

        raw_items = reward.get("items", [])
        item_names = []
        if isinstance(raw_items, list):
            item_names = [
                str(item_name).strip()
                for item_name in raw_items
                if str(item_name).strip()
            ]
        return {
            "gold": cls._non_negative_int(reward.get("gold")),
            "items": item_names,
        }

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _read_sync(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_data()

        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            corrupt_path = self.path.with_name(
                f"{self.path.stem}.corrupt-"
                f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}{self.path.suffix}"
            )
            try:
                os.replace(self.path, corrupt_path)
            except OSError:
                pass
            return self._empty_data()

        if not isinstance(loaded, dict):
            return self._empty_data()
        if not isinstance(loaded.get("users"), dict):
            loaded["users"] = {}
        loaded["schema_version"] = self.schema_version
        return loaded

    def _write_sync(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(data, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
