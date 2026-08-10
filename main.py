from __future__ import annotations

import asyncio
import base64
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from PIL import Image

from .local_renderer import LocalHtmlRenderer
from .storage import SignStore
from .template_pack import TemplatePack, TemplatePackError, TemplateRegistry

MORNING_MESSAGES = [
    "早上好，希望今天是美好的一天！",
    "醒了吗，今天也要元气满满哦！",
    "早上好呀，今天也要开心哦！",
    "早安，愿你拥有美好的一天！",
]

LATE_NIGHT_MESSAGES = [
    "今天要早点休息哦~",
    "可不要熬夜到太晚呀",
    "请尽早休息吧！",
    "不要熬夜啦！",
]

DEFAULT_MESSAGES = {
    "uid_prefix": "UID:",
    "sign_count_prefix": "累计签到",
    "sign_count_suffix": "天",
    "sign_title": "今日签到",
    "info_title": "我的信息",
    "bot_message_format": "{brand_name}说: {message}",
    "day_message": "{brand_name}希望你开心！",
    "reward_primary": "签到成功",
    "reward_gold": "金币 +{gold}",
    "reward_item": "{items}",
    "info_primary": "累计签到：{sign_count} 天",
    "info_gold": "总金币：{gold_balance}",
    "info_item": "上次签到：{last_sign_date}",
    "none_item": "暂无道具",
    "never_date": "还没有记录",
    "success_status": "签到成功",
    "already_status": "今日已签到",
    "not_signed_status": "尚未签到",
    "reserved_current_label": "当前好感度",
    "reserved_current": "--",
    "reserved_level_label": "好感度等级",
    "reserved_level_text": "未接入",
    "reserved_attitude": "对你的态度: 未接入",
    "reserved_next_text": "距离升级还差--好感度",
    "reserved_separator": ": ",
    "temperature_suffix": "℃",
}

FONT_FILES = {
    "cr105Font": "ChillReunion_105S.woff2",
    "cr65sFont": "ChillReunion_65S.woff2",
    "shFont": "SourceHanSansSC-Bold.woff2",
    "rxxxtFont": "rxxxkat.woff2",
    "kcytFont": "jcyt.woff2",
}

STATIC_IMAGE_FILES = {
    "calendar": "img/rl.png",
    "main_character": "img/1.png",
    "footer_character": "img/2.png",
    "heart_empty": "img/h1.png",
    "heart_full": "img/h2.png",
    "fallback_avatar": "img/1.png",
}

QQ_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=160"
CARD_WIDTH = 465
CARD_HEIGHT = 926
MAX_AVATAR_BYTES = 2 * 1024 * 1024
TEMPLATE_UPLOAD_PREFIX = "files/template_management/packages/"
SIGN_COMMAND_NAMES = ("签到", "打卡")
INFO_COMMAND_NAMES = ("我的签到", "签到状态")
COMMAND_PREFIXES = {
    "slash": "/",
    "none": "",
    "hash": "#",
}
RENDER_BACKENDS = {"auto", "local", "remote"}
_runtime_command_prefix = "/"


def _resolve_command_prefix(config: dict[str, Any]) -> str:
    mode = str(config.get("command_prefix_mode", "slash") or "slash").lower()
    if mode != "custom":
        return COMMAND_PREFIXES.get(mode, "/")
    custom = str(config.get("custom_command_prefix", "!") or "").strip()
    if not custom or len(custom) > 16 or any(char.isspace() for char in custom):
        return "!"
    return custom


def _resolve_render_backend(config: dict[str, Any]) -> str:
    backend = str(config.get("render_backend", "auto") or "auto").lower()
    return backend if backend in RENDER_BACKENDS else "auto"


def _set_runtime_command_prefix(prefix: str) -> None:
    global _runtime_command_prefix
    _runtime_command_prefix = prefix


def _event_command_text(event: AstrMessageEvent) -> str:
    message_obj = getattr(event, "message_obj", None)
    original = getattr(message_obj, "message_str", None)
    if isinstance(original, str):
        return original.strip()
    return str(event.get_message_str() or "").strip()


def _matches_configured_command(
    event: AstrMessageEvent,
    command_names: tuple[str, ...],
) -> bool:
    message = _event_command_text(event)
    return any(message == f"{_runtime_command_prefix}{name}" for name in command_names)


class _SignCommandFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return _matches_configured_command(event, SIGN_COMMAND_NAMES)


class _InfoCommandFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return _matches_configured_command(event, INFO_COMMAND_NAMES)


class ZhenxunSign(Star):
    """AstrBot implementation of the basic zhenxun sign-in flow."""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ) -> None:
        super().__init__(context, config)
        self.config = config if config is not None else {}
        self.command_prefix = _resolve_command_prefix(self.config)
        self.render_backend = _resolve_render_backend(self.config)
        _set_runtime_command_prefix(self.command_prefix)
        self.timezone_name = str(
            self.config.get("timezone", "Asia/Shanghai") or "Asia/Shanghai"
        )
        self.timezone = self._resolve_timezone(self.timezone_name)

        plugin_name = getattr(self, "name", "astrbot_plugin_zhenxun_sign")
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
        self.store = SignStore(self.data_dir / "sign_data.json")
        self.plugin_root = Path(__file__).parent
        self.template_path = self.plugin_root / "sign_card.html"
        self.style_path = self.plugin_root / "sign_card.css"
        self.asset_root = self.plugin_root / "assets" / "sign"
        self.template_registry = TemplateRegistry(
            self.plugin_root,
            self.data_dir / "template_packs",
        )
        self._asset_bundles: dict[str, tuple[str, dict[str, Any]]] = {}
        self._asset_lock = asyncio.Lock()
        self._avatar_cache: dict[str, str] = {}
        self.local_renderer = LocalHtmlRenderer(self.logger)
        self._template_error_snapshot: tuple[str, ...] = ()
        self._template_import_report: dict[str, Any] = {
            "checked": 0,
            "installed": [],
            "updated": [],
            "unchanged": [],
            "errors": [],
            "activated": "",
        }

    async def initialize(self) -> None:
        await asyncio.to_thread(self._migrate_legacy_template_selection)
        await asyncio.to_thread(self._import_configured_template_packages)
        packs, _ = await asyncio.to_thread(self._discover_templates)
        await asyncio.to_thread(self._normalise_selected_template, packs)
        self._sync_template_schema(packs)
        if self.render_backend != "remote":
            if await self.local_renderer.start():
                self.logger.info("Sign cards will use the local browser renderer")
            else:
                self.logger.warning(
                    "Local sign renderer is unavailable; using the remote T2I renderer"
                )

    async def terminate(self) -> None:
        await self.local_renderer.close()

    @filter.custom_filter(_SignCommandFilter)
    async def sign(self, event: AstrMessageEvent):
        """Record today's sign-in and return the original zhenxun-style card."""
        template_pack = self._active_template()
        user_id, platform, display_name, storage_key = self._identity(event)
        current_time = datetime.now(self.timezone)
        reward = self._build_reward()
        record, is_new_sign = await self.store.sign(
            key=storage_key,
            user_id=user_id,
            platform=platform,
            display_name=display_name,
            today=current_time.date().isoformat(),
            signed_at=current_time.isoformat(timespec="seconds"),
            reward=reward,
        )
        card_data = self._build_card_data(
            event=event,
            record=record,
            display_name=display_name,
            current_time=current_time,
            mode="sign" if is_new_sign else "view",
            is_new_sign=is_new_sign,
            reward=reward if is_new_sign else None,
            template_pack=template_pack,
        )
        self.logger.info(
            "Sign command handled: user=%s, new_sign=%s",
            user_id,
            is_new_sign,
        )
        yield await self._render_or_text(event, card_data, template_pack)

    @filter.custom_filter(_InfoCommandFilter)
    async def my_sign(self, event: AstrMessageEvent):
        """Return the user's current sign-in card without changing state."""
        template_pack = self._active_template()
        user_id, platform, display_name, storage_key = self._identity(event)
        current_time = datetime.now(self.timezone)
        record = await self.store.get_record(
            key=storage_key,
            user_id=user_id,
            platform=platform,
            display_name=display_name,
        )
        card_data = self._build_card_data(
            event=event,
            record=record,
            display_name=display_name,
            current_time=current_time,
            mode="view",
            is_new_sign=False,
            reward=None,
            template_pack=template_pack,
        )
        self.logger.info("My sign command handled: user=%s", user_id)
        yield await self._render_or_text(event, card_data, template_pack)

    def _import_configured_template_packages(self) -> None:
        report: dict[str, Any] = {
            "checked": 0,
            "installed": [],
            "updated": [],
            "unchanged": [],
            "errors": [],
            "activated": "",
        }
        management = self.config.get("template_management", {})
        if not isinstance(management, dict):
            report["errors"].append("模板管理配置不是对象")
            self._template_import_report = report
            return

        configured_paths = management.get("packages", [])
        if not isinstance(configured_paths, list):
            report["errors"].append("上传模板包配置不是文件列表")
            self._template_import_report = report
            return

        changed_packs: list[TemplatePack] = []
        for configured_path in configured_paths:
            report["checked"] += 1
            label = self._uploaded_template_label(configured_path)
            try:
                source_path = self._resolve_uploaded_template_path(configured_path)
                result = self.template_registry.install_zip(source_path)
            except (OSError, TemplatePackError) as error:
                message = f"{label}: {error}"
                report["errors"].append(message)
                self.logger.warning(
                    "Failed to import sign template upload: %s", message
                )
                continue

            report[result.action].append(result.pack.name)
            if result.action in {"installed", "updated"}:
                changed_packs.append(result.pack)
            self.logger.info(
                "Sign template upload %s: name=%s, source=%s",
                result.action,
                result.pack.name,
                label,
            )

        if changed_packs and bool(management.get("activate_latest", True)):
            selected = changed_packs[-1]
            self._set_selected_template_id(selected.id, persist=True)
            report["activated"] = selected.name

        self._template_import_report = report

    def _resolve_uploaded_template_path(self, configured_path: Any) -> Path:
        if not isinstance(configured_path, str):
            raise TemplatePackError("上传文件路径必须是字符串")
        normalised = configured_path.replace("\\", "/").lstrip("/")
        parts = [part for part in normalised.split("/") if part]
        if (
            not normalised.startswith(TEMPLATE_UPLOAD_PREFIX)
            or any(part in {".", ".."} for part in parts)
            or not parts
        ):
            raise TemplatePackError("上传文件路径不属于模板管理区")

        data_root = self.data_dir.resolve()
        source_path = data_root.joinpath(*parts).resolve()
        try:
            source_path.relative_to(data_root)
        except ValueError as error:
            raise TemplatePackError("上传文件路径越界") from error
        if source_path.suffix.lower() != ".zip":
            raise TemplatePackError("只支持 ZIP 模板包")
        if not source_path.is_file():
            raise TemplatePackError("上传文件不存在，请在设置页重新上传")
        return source_path

    @staticmethod
    def _uploaded_template_label(configured_path: Any) -> str:
        if not isinstance(configured_path, str):
            return "<无效文件>"
        return configured_path.replace("\\", "/").rsplit("/", 1)[-1] or "<无效文件>"

    def _discover_templates(self) -> tuple[dict[str, TemplatePack], list[str]]:
        packs, errors = self.template_registry.discover()
        snapshot = tuple(errors)
        if snapshot != self._template_error_snapshot:
            for error in errors:
                self.logger.warning("Ignored invalid sign template pack: %s", error)
            self._template_error_snapshot = snapshot
        return packs, errors

    def _active_template(
        self,
        packs: dict[str, TemplatePack] | None = None,
    ) -> TemplatePack:
        if packs is None:
            packs, _ = self._discover_templates()
        selected_id = self._selected_template_id()
        if selected := self._find_template_by_internal_id(packs, selected_id):
            return selected
        self.logger.warning(
            "Configured sign template '%s' was not found; using default",
            selected_id,
        )
        return packs["default"]

    @staticmethod
    def _find_template_by_internal_id(
        packs: dict[str, TemplatePack],
        template_id: str,
    ) -> TemplatePack | None:
        if selected := packs.get(template_id):
            return selected
        return next(
            (pack for pack in packs.values() if template_id in pack.legacy_ids),
            None,
        )

    def _selected_template_id(self) -> str:
        management = self.config.get("template_management", {})
        selected_id = (
            management.get("active_template", "default")
            if isinstance(management, dict)
            else "default"
        )
        selected_id = str(selected_id or "default").strip()
        return selected_id.lower() or "default"

    def _migrate_legacy_template_selection(self) -> None:
        legacy_path = self.data_dir / "active_template.txt"
        try:
            legacy_id = legacy_path.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return
        if legacy_id and self._selected_template_id() == "default":
            self._set_selected_template_id(legacy_id, persist=True)
        legacy_path.unlink(missing_ok=True)

    def _set_selected_template_id(
        self, template_id: str, persist: bool = False
    ) -> None:
        management = self.config.get("template_management", {})
        if not isinstance(management, dict):
            management = {}
            self.config["template_management"] = management
        management["active_template"] = template_id
        if persist:
            save_config = getattr(self.config, "save_config", None)
            if callable(save_config):
                save_config()

    def _normalise_selected_template(
        self,
        packs: dict[str, TemplatePack],
    ) -> TemplatePack:
        selected_id = self._selected_template_id()
        selected = self._find_template_by_internal_id(packs, selected_id)
        if selected is None:
            selected = packs["default"]
        if selected.id != selected_id:
            self._set_selected_template_id(selected.id, persist=True)
        return selected

    def _sync_template_schema(self, packs: dict[str, TemplatePack]) -> None:
        schema = getattr(self.config, "schema", None)
        if not isinstance(schema, dict):
            return
        management = schema.get("template_management", {})
        items = management.get("items", {}) if isinstance(management, dict) else {}
        field = items.get("active_template") if isinstance(items, dict) else None
        if not isinstance(field, dict):
            return

        name_counts: dict[str, int] = {}
        labels: list[str] = []
        for pack in packs.values():
            count = name_counts.get(pack.name, 0) + 1
            name_counts[pack.name] = count
            labels.append(pack.name if count == 1 else f"{pack.name} ({count})")
        field["options"] = list(packs)
        field["labels"] = labels

    def _identity(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, str, str, str]:
        user_id = event.get_sender_id() or event.session_id
        platform = event.get_platform_id() or event.get_platform_name()
        display_name = event.get_sender_name() or user_id
        storage_key = f"{platform}::{user_id}"
        return user_id, platform, display_name, storage_key

    def _build_reward(self) -> dict[str, Any]:
        reward_config = self.config.get("rewards", {})
        if not isinstance(reward_config, dict):
            reward_config = {}

        gold = 0
        if bool(reward_config.get("enable_gold", False)):
            minimum_gold = self._as_non_negative_int(reward_config.get("gold_min", 1))
            maximum_gold = self._as_non_negative_int(reward_config.get("gold_max", 100))
            if maximum_gold < minimum_gold:
                minimum_gold, maximum_gold = maximum_gold, minimum_gold
            gold = random.randint(minimum_gold, maximum_gold)

        items: list[str] = []
        if bool(reward_config.get("enable_item_placeholder", False)):
            raw_item_pool = reward_config.get("item_pool", [])
            if isinstance(raw_item_pool, list):
                item_pool = [
                    str(item_name).strip()
                    for item_name in raw_item_pool
                    if str(item_name).strip()
                ]
                if item_pool:
                    items.append(random.choice(item_pool))
        return {"gold": gold, "items": items}

    def _build_card_data(
        self,
        event: AstrMessageEvent,
        record: dict[str, Any],
        display_name: str,
        current_time: datetime,
        mode: str,
        is_new_sign: bool,
        reward: dict[str, Any] | None,
        template_pack: TemplatePack | None = None,
    ) -> dict[str, Any]:
        template_settings = template_pack.settings if template_pack else {}
        messages = self._messages(template_settings)
        brand_name = self._template_text(
            template_settings,
            "brand_name",
            "真寻",
        )
        date_format = self._template_text(template_settings, "date_format", "iso")
        date_text = self._format_card_datetime(current_time, date_format)
        last_sign_date = str(record.get("last_sign_date") or "")
        last_date_text = self._format_stored_date(last_sign_date, messages)
        sign_count = self._as_non_negative_int(record.get("sign_count", 0))
        gold_balance = self._as_non_negative_int(record.get("gold_balance", 0))

        reward_data = reward if isinstance(reward, dict) else {"gold": 0, "items": []}
        reward_gold = self._as_non_negative_int(reward_data.get("gold", 0))
        raw_items = reward_data.get("items", [])
        reward_items = (
            [str(item).strip() for item in raw_items if str(item).strip()]
            if isinstance(raw_items, list)
            else []
        )
        item_text = "、".join(reward_items) or messages["none_item"]

        if is_new_sign:
            status_text = messages["success_status"]
        elif last_sign_date:
            status_text = messages["already_status"]
        else:
            status_text = messages["not_signed_status"]

        format_values = {
            "brand_name": brand_name,
            "user_name": display_name,
            "date": date_text,
            "time": current_time.strftime("%H:%M:%S"),
            "last_sign_date": last_date_text,
            "sign_count": sign_count,
            "gold": reward_gold,
            "gold_balance": gold_balance,
            "items": item_text,
            "status": status_text,
        }
        bot_message = self._build_bot_message(
            current_time,
            messages,
            format_values,
            template_settings,
        )
        weather_names = (
            template_pack.weather_names
            if template_pack and template_pack.weather_names
            else tuple(f"{index}.png" for index in range(12))
        )
        tag_names = (
            template_pack.tag_names
            if template_pack and template_pack.tag_names
            else tuple(f"{index}.png" for index in range(6))
        )
        page = {
            "date_str": date_text,
            "weather_icon_name": random.choice(weather_names),
            "temperature": random.randint(1, 40),
            "tag_icon_name": random.choice(tag_names),
        }

        return {
            "brand_name": brand_name,
            "mode": mode,
            "is_card_view": mode == "view",
            "user": {
                "nickname": display_name,
                "uid_str": self._format_uid(str(record.get("user_id") or "")),
                "avatar_source": self._avatar_source(
                    event,
                    format_values,
                    template_settings,
                ),
                "sign_count": sign_count,
                "font_size": 27 if len(display_name) > 6 else 45,
            },
            "reward": {
                "gold": reward_gold,
                "items": reward_items,
                "primary_text": self._format_text(
                    messages["reward_primary"], format_values
                ),
                "gold_text": self._format_text(messages["reward_gold"], format_values),
                "item_text": self._format_text(messages["reward_item"], format_values),
            },
            "info": {
                "primary_text": self._format_text(
                    messages["info_primary"], format_values
                ),
                "gold_text": self._format_text(messages["info_gold"], format_values),
                "item_text": self._format_text(messages["info_item"], format_values),
            },
            "reserved": self._reserved_panel(
                messages,
                format_values,
                template_settings,
            ),
            "page": page,
            "bot_message": bot_message,
            "labels": messages,
            "date": date_text,
            "last_sign_date": last_date_text,
            "sign_count": sign_count,
            "gold_balance": gold_balance,
            "inventory": record.get("items", {}),
            "status_text": status_text,
        }

    def _build_bot_message(
        self,
        current_time: datetime,
        messages: dict[str, str],
        format_values: dict[str, Any],
        template_settings: dict[str, Any] | None = None,
    ) -> str:
        if 6 < current_time.hour < 10:
            message_pool = self._configured_message_pool(
                "morning_messages",
                MORNING_MESSAGES,
                template_settings,
            )
        elif 0 <= current_time.hour < 6:
            message_pool = self._configured_message_pool(
                "late_night_messages",
                LATE_NIGHT_MESSAGES,
                template_settings,
            )
        else:
            message_pool = [messages["day_message"]]

        message = self._format_text(random.choice(message_pool), format_values)
        return self._format_text(
            messages["bot_message_format"],
            format_values | {"message": message},
        )

    def _configured_message_pool(
        self,
        key: str,
        defaults: list[str],
        template_settings: dict[str, Any] | None = None,
    ) -> list[str]:
        configured = (
            template_settings.get(key, defaults) if template_settings else defaults
        )
        if not isinstance(configured, list):
            return defaults
        values = [str(message) for message in configured if str(message)]
        return values or defaults

    def _reserved_panel(
        self,
        messages: dict[str, str],
        format_values: dict[str, Any],
        template_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template_panel = (
            template_settings.get("reserved_panel", {}) if template_settings else {}
        )
        configured = dict(template_panel) if isinstance(template_panel, dict) else {}

        heart_count = min(
            8,
            self._as_non_negative_int(configured.get("heart_count", 8)),
        )
        filled_hearts = min(
            heart_count,
            self._as_non_negative_int(configured.get("filled_hearts", 0)),
        )

        def panel_text(key: str, default_key: str) -> str:
            raw_value = configured.get(key, messages[default_key])
            value = messages[default_key] if raw_value is None else str(raw_value)
            return self._format_text(value, format_values)

        return {
            "current_label": panel_text(
                "current_label",
                "reserved_current_label",
            ),
            "current": panel_text("current", "reserved_current"),
            "level_label": panel_text("level_label", "reserved_level_label"),
            "level_text": panel_text("level_text", "reserved_level_text"),
            "attitude": panel_text("attitude", "reserved_attitude"),
            "next_text": panel_text("next_text", "reserved_next_text"),
            "separator": str(
                configured.get("separator", messages["reserved_separator"])
            ),
            "progress": self._as_percentage(configured.get("progress", 0)),
            "heart2": [True] * filled_hearts,
            "heart1": [True] * (heart_count - filled_hearts),
        }

    async def _render_or_text(
        self,
        event: AstrMessageEvent,
        card_data: dict[str, Any],
        template_pack: TemplatePack | None = None,
    ):
        fallback = self._fallback_text(card_data)
        selected_pack = template_pack or self._active_template()
        try:
            return await self._render_card(event, card_data, selected_pack)
        except Exception as error:
            self.logger.warning(
                "Sign card rendering failed with template %s: %s",
                selected_pack.id,
                error,
            )
            if selected_pack.id != "default":
                try:
                    default_pack = self.template_registry.legacy_pack()
                    return await self._render_card(event, card_data, default_pack)
                except Exception as fallback_error:
                    self.logger.warning(
                        "Default sign card fallback rendering failed: %s",
                        fallback_error,
                    )
        return event.plain_result(fallback)

    async def _render_card(
        self,
        event: AstrMessageEvent,
        card_data: dict[str, Any],
        template_pack: TemplatePack,
    ):
        render_started = time.perf_counter()
        template = await asyncio.to_thread(
            template_pack.read_text,
            template_pack.template_file,
        )
        render_data = await self._prepare_render_data(card_data, template_pack)
        image_path, backend = await self._render_template_image(
            template,
            render_data,
            template_pack,
        )
        image_path = await asyncio.to_thread(
            self._crop_card,
            image_path,
            template_pack.width,
            template_pack.height,
        )
        self.logger.debug("Sign card rendered with %s backend", backend)
        self.logger.debug(
            "Sign card total render time: %.0f ms",
            (time.perf_counter() - render_started) * 1000,
        )
        return event.image_result(image_path)

    async def _render_template_image(
        self,
        template: str,
        render_data: dict[str, Any],
        template_pack: TemplatePack,
    ) -> tuple[str, str]:
        render_started = time.perf_counter()
        if self.render_backend != "remote" and await self.local_renderer.start():
            try:
                image_path = await self.local_renderer.render(
                    template,
                    render_data,
                    width=template_pack.width,
                    height=template_pack.height,
                    template_key=template_pack.fingerprint,
                )
                self.logger.debug(
                    "Sign card local render completed in %.0f ms",
                    (time.perf_counter() - render_started) * 1000,
                )
                return image_path, "local"
            except Exception as error:
                self.logger.warning(
                    "Local sign card rendering failed; falling back to remote T2I: %s",
                    error,
                )

        image_path = await self.html_render(
            template,
            render_data,
            return_url=False,
            options={
                "type": "png",
                "full_page": True,
                "animations": "disabled",
                "scale": "css",
            },
        )
        self.logger.debug(
            "Sign card remote render completed in %.0f ms",
            (time.perf_counter() - render_started) * 1000,
        )
        return image_path, "remote"

    async def _prepare_render_data(
        self,
        card_data: dict[str, Any],
        template_pack: TemplatePack | None = None,
    ) -> dict[str, Any]:
        sign_style, image_assets = await self._get_asset_bundle(template_pack)
        page = card_data["page"]
        tag_name = str(page.get("tag_icon_name") or "0.png")
        weather_name = str(page.get("weather_icon_name") or "0.png")
        user = card_data["user"]
        avatar_url = image_assets.get("template_avatar")
        if not avatar_url:
            avatar_url = await self._avatar_data_uri(
                str(user.get("avatar_source") or ""),
                image_assets["fallback_avatar"],
            )
        tag_fallback = next(iter(image_assets["tags"].values()))
        weather_fallback = next(iter(image_assets["weather"].values()))

        return card_data | {
            "sign_style": sign_style,
            "assets": {
                "avatar_url": avatar_url,
                "calendar": image_assets["calendar"],
                "main_character": image_assets["main_character"],
                "footer_character": image_assets["footer_character"],
                "heart_empty": image_assets["heart_empty"],
                "heart_full": image_assets["heart_full"],
                "tag": image_assets["tags"].get(
                    tag_name,
                    tag_fallback,
                ),
                "weather": image_assets["weather"].get(
                    weather_name,
                    weather_fallback,
                ),
            },
        }

    async def _get_asset_bundle(
        self,
        template_pack: TemplatePack | None = None,
    ) -> tuple[str, dict[str, Any]]:
        pack = template_pack or self.template_registry.legacy_pack()
        if cached := self._asset_bundles.get(pack.fingerprint):
            return cached

        async with self._asset_lock:
            if cached := self._asset_bundles.get(pack.fingerprint):
                return cached
            bundle = await asyncio.to_thread(self._load_asset_bundle_sync, pack)
            if len(self._asset_bundles) >= 12:
                self._asset_bundles.pop(next(iter(self._asset_bundles)))
            self._asset_bundles[pack.fingerprint] = bundle
            return bundle

    def _load_asset_bundle_sync(
        self,
        template_pack: TemplatePack | None = None,
    ) -> tuple[str, dict[str, Any]]:
        pack = template_pack or self.template_registry.legacy_pack()
        font_paths = {
            family_name: pack.asset_path(f"fonts/{file_name}")
            for family_name, file_name in FONT_FILES.items()
        }
        static_paths = {
            key: pack.asset_path(relative_path)
            for key, relative_path in STATIC_IMAGE_FILES.items()
        }
        tag_paths = {
            name: pack.asset_path(f"img/tag/{name}") for name in pack.tag_names
        }
        weather_paths = {
            name: pack.asset_path(f"img/weather/{name}") for name in pack.weather_names
        }
        if not tag_paths or not weather_paths:
            raise ValueError(f"template {pack.id} has no tag or weather images")

        avatar_asset = str(pack.settings.get("avatar_asset") or "").strip()
        avatar_path = pack.asset_path(avatar_asset) if avatar_asset else ""
        requested_paths = [
            pack.style_file,
            *[path for path in font_paths.values() if pack.exists(path)],
            *static_paths.values(),
            *tag_paths.values(),
            *weather_paths.values(),
        ]
        if avatar_path:
            requested_paths.append(avatar_path)
        payload = pack.read_many(tuple(dict.fromkeys(requested_paths)))

        font_rules = []
        for family_name, relative_path in font_paths.items():
            if relative_path not in payload:
                continue
            font_uri = self._bytes_data_uri(payload[relative_path], "font/woff2")
            font_rules.append(
                f'@font-face {{ font-family: "{family_name}"; '
                f'src: url("{font_uri}"); }}'
            )

        theme_style = """
:root {
    --color-sign-page-bg: #FBE4E4;
    --color-sign-avatar-shadow: #D6A7A7;
    --color-sign-nickname-text: #D37B8D;
    --color-sign-primary: #D47E8F;
    --color-sign-primary-dark: #953B50;
    --color-sign-divider: #D1778A;
    --color-sign-progress-border: #DF9DA8;
}

body {
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}
""".strip()
        official_style = payload[pack.style_file].decode("utf-8")
        sign_style = "\n\n".join([theme_style, "\n".join(font_rules), official_style])

        image_assets: dict[str, Any] = {
            key: self._bytes_data_uri(
                payload[relative_path],
                self._image_mime_type(Path(relative_path).suffix),
            )
            for key, relative_path in static_paths.items()
        }
        image_assets["tags"] = {
            name: self._bytes_data_uri(
                payload[relative_path],
                self._image_mime_type(Path(relative_path).suffix),
            )
            for name, relative_path in tag_paths.items()
        }
        image_assets["weather"] = {
            name: self._bytes_data_uri(
                payload[relative_path],
                self._image_mime_type(Path(relative_path).suffix),
            )
            for name, relative_path in weather_paths.items()
        }
        image_assets["template_avatar"] = (
            self._bytes_data_uri(
                payload[avatar_path],
                self._image_mime_type(Path(avatar_path).suffix),
            )
            if avatar_path
            else ""
        )
        return sign_style, image_assets

    async def _avatar_data_uri(self, source: str, fallback: str) -> str:
        if not source:
            return fallback
        if source.startswith("data:image/"):
            return source
        if cached := self._avatar_cache.get(source):
            return cached

        try:
            local_path = Path(source).expanduser()
            if not local_path.is_absolute():
                local_path = Path(__file__).parent / local_path
            if local_path.is_file():
                mime_type = self._image_mime_type(local_path.suffix)
                avatar = await asyncio.to_thread(
                    self._path_data_uri,
                    local_path,
                    mime_type,
                )
            elif source.startswith(("http://", "https://")):
                timeout = aiohttp.ClientTimeout(total=8)
                async with (
                    aiohttp.ClientSession(timeout=timeout, trust_env=True) as session,
                    session.get(source, allow_redirects=True) as response,
                ):
                    response.raise_for_status()
                    content = await response.content.read(MAX_AVATAR_BYTES + 1)
                    if len(content) > MAX_AVATAR_BYTES:
                        raise ValueError("avatar exceeds size limit")
                    mime_type = response.headers.get("Content-Type", "image/png")
                    mime_type = mime_type.split(";", 1)[0].strip().lower()
                    if not mime_type.startswith("image/"):
                        raise ValueError("avatar response is not an image")
                    avatar = self._bytes_data_uri(content, mime_type)
            else:
                return fallback
        except Exception as error:
            self.logger.debug("Avatar loading failed: %s", error)
            return fallback

        if len(self._avatar_cache) >= 128:
            self._avatar_cache.pop(next(iter(self._avatar_cache)))
        self._avatar_cache[source] = avatar
        return avatar

    def _avatar_source(
        self,
        event: AstrMessageEvent,
        format_values: dict[str, Any],
        template_settings: dict[str, Any] | None = None,
    ) -> str:
        configured = self._template_text(
            template_settings or {},
            "avatar_url",
            "",
        )
        if configured:
            return self._format_text(configured, format_values)

        user_id = event.get_sender_id()
        platform = f"{event.get_platform_name()} {event.get_platform_id()}".lower()
        if user_id.isdigit() and any(
            platform_name in platform for platform_name in ("aiocqhttp", "onebot", "qq")
        ):
            return QQ_AVATAR_URL.format(user_id=user_id)
        return ""

    def _fallback_text(self, card_data: dict[str, Any]) -> str:
        labels = card_data["labels"]
        user = card_data["user"]
        if card_data["is_card_view"]:
            title = labels["info_title"]
            detail = card_data["info"]
        else:
            title = labels["sign_title"]
            detail = card_data["reward"]

        return "\n".join(
            [
                f"{card_data['brand_name']} · {title}",
                card_data["bot_message"],
                (
                    f"{labels['sign_count_prefix']}"
                    f"{user['sign_count']}"
                    f"{labels['sign_count_suffix']}"
                ),
                detail["primary_text"],
                detail["gold_text"],
                detail["item_text"],
                card_data["date"],
            ]
        )

    def _messages(
        self,
        template_settings: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        template_messages = (
            template_settings.get("messages", {}) if template_settings else {}
        )
        configured_messages = (
            dict(template_messages) if isinstance(template_messages, dict) else {}
        )

        messages = {}
        for key, default in DEFAULT_MESSAGES.items():
            configured = configured_messages.get(key, default)
            messages[key] = default if configured is None else str(configured)
        return messages

    def _format_uid(self, user_id: str) -> str:
        if not bool(self.config.get("show_user_id", True)):
            return "XXXX XXXX XXXX"

        configured = self._config_text("uid_value", "")
        uid = configured or user_id
        if uid.isdigit():
            normalized = uid.rjust(12, "0")[-12:]
            return f"{normalized[:4]} {normalized[4:8]} {normalized[8:]}"
        return uid or "XXXX XXXX XXXX"

    def _format_stored_date(
        self,
        stored_date: str,
        messages: dict[str, str],
    ) -> str:
        if not stored_date:
            return messages["never_date"]
        try:
            return datetime.strptime(stored_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return stored_date

    @staticmethod
    def _format_card_datetime(current_time: datetime, time_format: str) -> str:
        if not time_format or time_format.lower() == "iso":
            return current_time.replace(microsecond=0).isoformat(sep=" ")
        try:
            return current_time.strftime(time_format)
        except (TypeError, ValueError):
            return current_time.replace(microsecond=0).isoformat(sep=" ")

    def _config_text(self, key: str, default: str) -> str:
        configured = self.config.get(key, default)
        return default if configured is None else str(configured)

    def _template_text(
        self,
        template_settings: dict[str, Any],
        key: str,
        default: str,
    ) -> str:
        if key not in template_settings:
            return default
        configured = template_settings[key]
        return default if configured is None else str(configured)

    @staticmethod
    def _format_text(template: str, values: dict[str, Any]) -> str:
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            return template

    @staticmethod
    def _as_non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _as_percentage(value: Any) -> float:
        try:
            return min(100.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _path_data_uri(path: Path, mime_type: str) -> str:
        return ZhenxunSign._bytes_data_uri(path.read_bytes(), mime_type)

    @staticmethod
    def _bytes_data_uri(content: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _image_mime_type(suffix: str) -> str:
        return {
            ".gif": "image/gif",
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix.lower(), "image/png")

    @staticmethod
    def _crop_card(
        image_path: str,
        card_width: int = CARD_WIDTH,
        card_height: int = CARD_HEIGHT,
    ) -> str:
        path = Path(image_path)
        with Image.open(path) as source_image:
            width, height = source_image.size
            if width < card_width or height < card_height:
                raise ValueError(
                    f"rendered card is too small: {width}x{height}, "
                    f"expected at least {card_width}x{card_height}"
                )
            if (width, height) == (
                card_width,
                card_height,
            ) and path.suffix.lower() == ".png":
                return str(path)
            cropped = source_image.crop((0, 0, card_width, card_height)).copy()
        target_path = (
            path if path.suffix.lower() == ".png" else path.with_suffix(".png")
        )
        try:
            cropped.save(target_path, format="PNG")
        finally:
            cropped.close()
        if target_path != path:
            path.unlink(missing_ok=True)
        return str(target_path)

    @staticmethod
    def _resolve_timezone(timezone_name: str):
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if timezone_name == "Asia/Shanghai":
                return timezone(timedelta(hours=8), "Asia/Shanghai")
            return datetime.now().astimezone().tzinfo or timezone.utc
