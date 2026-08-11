# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2>=3.1,<4", "pillow>=11,<13"]
# ///

from __future__ import annotations

import argparse
import base64
import copy
import html
import json
import mimetypes
import re
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image, ImageOps

EDITOR_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = EDITOR_ROOT.parent
TEMPLATE_PATH = PLUGIN_ROOT / "sign_card.html"
STYLE_PATH = PLUGIN_ROOT / "sign_card.css"
MANIFEST_PATH = PLUGIN_ROOT / "sign_card.manifest.json"
SOURCE_ASSET_ROOT = PLUGIN_ROOT / "assets" / "sign"
DEFAULT_STATE_PATH = EDITOR_ROOT / "default_state.json"
UPLOAD_ROOT = EDITOR_ROOT / "workspace" / "uploads"
OUTPUT_ROOT = EDITOR_ROOT / "output"
TEMPLATE_PACK_ROOT = PLUGIN_ROOT / "template_packs"
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
PACK_FORMAT = "zhenxun-astr-template"
PACK_VERSION = 1

FONT_FILES = {
    "cr105Font": "ChillReunion_105S.woff2",
    "cr65sFont": "ChillReunion_65S.woff2",
    "shFont": "SourceHanSansSC-Bold.woff2",
    "rxxxtFont": "rxxxkat.woff2",
    "kcytFont": "jcyt.woff2",
}

FONT_FALLBACK_STYLE = """
.sign-data {
    font-family: 'kcytFont', 'shFont', sans-serif;
}

.bottom-foot {
    font-family: 'rxxxtFont', 'shFont', sans-serif;
}
""".strip()

ASSET_TARGETS = {
    "calendar": "img/rl.png",
    "mainCharacter": "img/1.png",
    "footerCharacter": "img/2.png",
    "heartEmpty": "img/h1.png",
    "heartFull": "img/h2.png",
}
for _index in range(6):
    ASSET_TARGETS[f"tag:{_index}"] = f"img/tag/{_index}.png"
for _index in range(12):
    ASSET_TARGETS[f"weather:{_index}"] = f"img/weather/{_index}.png"
for _family, _filename in FONT_FILES.items():
    ASSET_TARGETS[f"font:{_family}"] = f"fonts/{_filename}"

SOURCE_ASSET_DEFAULTS = {
    "calendar": "img/rl.png",
    "mainCharacter": "img/1.png",
    "footerCharacter": "img/2.png",
    "heartEmpty": "img/h1.png",
    "heartFull": "img/h2.png",
}

LAYER_SPECS = {
    "wrapper": (".wrapper", "box"),
    "avatar": (".avatar", "image"),
    "nickname": (".nickname", "text"),
    "uid": (".uid", "text"),
    "calendar": (".rl-img", "image"),
    "mainCharacter": (".zx-img", "image"),
    "signCount": (".text-day", "text"),
    "botMessage": (".text-zx", "text"),
    "signTag": (".qian", "image"),
    "title": (".today-text", "text"),
    "rewardPrimary": (".sign-data > .abs-text:nth-of-type(1) .gift", "text"),
    "rewardGold": (".sign-data > .abs-text:nth-of-type(2) .gift", "text"),
    "rewardItem": (".sign-data > .abs-text:nth-of-type(3) .gift", "text"),
    "divider": (".line", "box"),
    "current": (".cur-text", "text"),
    "hearts": (".heart-list", "image"),
    "level": (".bot-text > p:nth-child(1)", "text"),
    "attitude": (".bot-text > p:nth-child(2)", "text"),
    "next": (".bot-text > p:nth-child(3)", "text"),
    "progressBorder": (".progress-border", "box"),
    "progressBar": (".progress-bar", "box"),
    "weather": (".weather-img", "image"),
    "temperature": (".wd", "text"),
    "footerCharacter": (".mbl-img", "image"),
    "date": (".date", "text"),
}

THEME_STYLE = """
:root {
    --color-sign-page-bg: __PAGE_BG__;
    --color-sign-avatar-shadow: __AVATAR_SHADOW__;
    --color-sign-nickname-text: __NICKNAME_TEXT__;
    --color-sign-primary: __PRIMARY__;
    --color-sign-primary-dark: __PRIMARY_DARK__;
    --color-sign-divider: __DIVIDER__;
    --color-sign-progress-border: __PROGRESS_BORDER__;
}

body {
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}
""".strip()

COLOR_RE = re.compile(
    r"^(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([0-9 .,/%+-]+\)|hsla?\([0-9 .,/%+-]+\))$"
)
SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_'\- ,]+$")
SAFE_SHADOW_RE = re.compile(r"^[a-zA-Z0-9 #(),.%+\-]+$")


def _ensure_workspace() -> None:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def _load_default_state() -> dict[str, Any]:
    with DEFAULT_STATE_PATH.open("r", encoding="utf-8") as state_file:
        return json.load(state_file)


def _merge_defaults(default: Any, value: Any) -> Any:
    if isinstance(default, dict) and isinstance(value, dict):
        merged = copy.deepcopy(default)
        for key, item in value.items():
            merged[key] = _merge_defaults(merged[key], item) if key in merged else item
        return merged
    return copy.deepcopy(value)


def _normalise_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("编辑项目必须是 JSON 对象")
    state = _merge_defaults(_load_default_state(), value)
    state["schemaVersion"] = 1
    template_metadata = state.get("templateMeta", {})
    if isinstance(template_metadata, dict):
        template_metadata.pop("id", None)
    return state


def _safe_path(root: Path, relative: str) -> Path | None:
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _ref_path(ref: Any) -> Path | None:
    if not isinstance(ref, dict):
        return None
    filename = str(ref.get("file", ""))
    if not filename or Path(filename).name != filename:
        return None
    path = _safe_path(UPLOAD_ROOT, filename)
    return path if path and path.is_file() else None


def _source_url(relative: str) -> str:
    return f"/source-assets/{quote(relative.replace('\\', '/'), safe='/')}"


def _uploaded_url(ref: Any) -> str | None:
    path = _ref_path(ref)
    if not path:
        return None
    return f"/uploaded-assets/{quote(path.name)}"


def _asset_url(state: dict[str, Any], slot: str, default_relative: str) -> str:
    overrides = state.get("assets", {}).get("overrides", {})
    url = _uploaded_url(overrides.get(slot))
    return url or _source_url(default_relative)


def _font_url(state: dict[str, Any], family: str) -> str:
    default_relative = f"fonts/{FONT_FILES[family]}"
    return _asset_url(state, f"font:{family}", default_relative)


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _number(value: Any, default: float, minimum: float, maximum: float) -> str:
    return f"{_as_float(value, default, minimum, maximum):.4g}"


def _css_color(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    if text and COLOR_RE.fullmatch(text):
        return text
    return default


def _css_token(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if text and SAFE_TOKEN_RE.fullmatch(text):
        return text
    return default


def _format_text(value: Any, values: dict[str, Any]) -> str:
    text = str(value or "")
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


def _build_template_data(state: dict[str, Any]) -> dict[str, Any]:
    preview = state["preview"]
    content = state["content"]
    sign_count = max(0, int(_as_float(preview.get("signCount"), 0, 0, 999999)))
    temperature = int(_as_float(preview.get("temperature"), 23, -50, 60))
    mode = "view" if preview.get("mode") == "view" else "sign"
    brand_name = str(content.get("brandName") or "真寻")
    format_values = {
        "brand_name": brand_name,
        "user_name": str(preview.get("nickname") or "预览用户"),
        "date": str(preview.get("date") or ""),
        "time": str(preview.get("date") or "").split(" ")[-1],
        "last_sign_date": str(preview.get("date") or ""),
        "sign_count": sign_count,
        "gold": 0,
        "gold_balance": 0,
        "items": str(content.get("noneItem") or "暂无道具"),
        "status": str(content.get("successStatus") or "签到成功"),
        "favour": 88,
        "favour_level": "4 [熟悉]",
        "favour_provider_level": "普通",
        "favour_zhenxun_level": 4,
        "favour_zhenxun_relation": "熟悉",
        "favour_level_min": 0,
        "favour_level_max": 149,
        "favour_relationship": "无",
        "favour_attitude": "是个好人",
        "favour_next": 62,
        "favour_next_level": "喜欢",
        "favour_progress": 59.06,
        "favour_delta": 1,
        "favour_status": "preview",
        "favour_available": True,
        "favour_is_max": False,
    }
    day_message = _format_text(content.get("dayMessage"), format_values)
    bot_message = str(preview.get("botMessage") or "")
    if not bot_message:
        bot_message = _format_text(
            content.get("botMessageFormat"), format_values | {"message": day_message}
        )
    heart_count = int(_as_float(content.get("heartCount"), 8, 0, 16))
    filled_hearts = min(
        heart_count,
        int(_as_float(content.get("filledHearts"), 0, 0, 16)),
    )
    reserved = {
        "current_label": _format_text(
            content.get("reservedCurrentLabel"), format_values
        ),
        "current": _format_text(content.get("reservedCurrent"), format_values),
        "level_label": _format_text(content.get("reservedLevelLabel"), format_values),
        "level_text": _format_text(content.get("reservedLevelText"), format_values),
        "attitude": _format_text(content.get("reservedAttitude"), format_values),
        "next_text": _format_text(content.get("reservedNextText"), format_values),
        "separator": str(content.get("reservedSeparator") or ": "),
        "progress": _as_float(content.get("reservedProgress"), 0, 0, 100),
        "heart2": [True] * filled_hearts,
        "heart1": [True] * (heart_count - filled_hearts),
    }
    reward = {
        "primary_text": _format_text(content.get("rewardPrimary"), format_values),
        "gold_text": _format_text(content.get("rewardGold"), format_values),
        "item_text": _format_text(content.get("rewardItem"), format_values),
    }
    info = {
        "primary_text": _format_text(content.get("infoPrimary"), format_values),
        "gold_text": _format_text(content.get("infoGold"), format_values),
        "item_text": _format_text(content.get("infoItem"), format_values),
    }
    page = {
        "date_str": str(preview.get("date") or ""),
        "weather_icon_name": f"{int(_as_float(preview.get('weather'), 0, 0, 11))}.png",
        "temperature": temperature,
        "tag_icon_name": f"{int(_as_float(preview.get('tag'), 0, 0, 5))}.png",
    }
    return {
        "is_card_view": mode == "view",
        "user": {
            "nickname": str(preview.get("nickname") or "预览用户"),
            "uid_str": str(preview.get("uid") or "XXXX XXXX XXXX"),
            "sign_count": sign_count,
            "font_size": 27 if len(str(preview.get("nickname") or "")) > 6 else 45,
        },
        "reward": reward,
        "info": info,
        "reserved": reserved,
        "page": page,
        "bot_message": bot_message,
        "labels": {
            "uid_prefix": str(content.get("uidPrefix") or "UID:"),
            "sign_count_prefix": str(content.get("signCountPrefix") or "累计签到"),
            "sign_count_suffix": str(content.get("signCountSuffix") or "天"),
            "sign_title": str(content.get("signTitle") or "今日签到"),
            "info_title": str(content.get("infoTitle") or "我的信息"),
            "temperature_suffix": str(content.get("temperatureSuffix") or "℃"),
        },
    }


def _theme_value(theme: dict[str, Any], key: str, default: str) -> str:
    return _css_color(theme.get(key), default)


def _theme_style(theme: dict[str, Any]) -> str:
    values = {
        "__PAGE_BG__": _theme_value(theme, "pageBg", "#FBE4E4"),
        "__AVATAR_SHADOW__": _theme_value(theme, "avatarShadow", "#D6A7A7"),
        "__NICKNAME_TEXT__": _theme_value(theme, "nicknameText", "#D37B8D"),
        "__PRIMARY__": _theme_value(theme, "primary", "#D47E8F"),
        "__PRIMARY_DARK__": _theme_value(theme, "primaryDark", "#953B50"),
        "__DIVIDER__": _theme_value(theme, "divider", "#D1778A"),
        "__PROGRESS_BORDER__": _theme_value(theme, "progressBorder", "#DF9DA8"),
    }
    result = THEME_STYLE
    for placeholder, value in values.items():
        result = result.replace(placeholder, value)
    return result


def _layer_transform(layer: dict[str, Any]) -> str:
    x = _number(layer.get("x"), 0, -2000, 2000)
    y = _number(layer.get("y"), 0, -2000, 2000)
    rotation = _number(layer.get("rotation"), 0, -360, 360)
    scale_x = _number(layer.get("scaleX"), 1, 0.01, 20)
    scale_y = _number(layer.get("scaleY"), 1, 0.01, 20)
    return f"translate({x}px, {y}px) rotate({rotation}deg) scale({scale_x}, {scale_y})"


def _layer_css(
    layer_id: str,
    layer: dict[str, Any],
    default_layer: dict[str, Any],
) -> str:
    spec = LAYER_SPECS.get(layer_id)
    if not spec:
        return ""
    selector, kind = spec
    rules: list[str] = []
    transform_keys = ("x", "y", "rotation", "scaleX", "scaleY")
    if any(layer.get(key) != default_layer.get(key) for key in transform_keys):
        rules.extend(
            [
                f"transform: {_layer_transform(layer)} !important;",
                "transform-origin: center center !important;",
            ]
        )
    if layer.get("opacity") != default_layer.get("opacity"):
        rules.append(f"opacity: {_number(layer.get('opacity'), 1, 0, 1)} !important;")
    if layer.get("zIndex") != default_layer.get("zIndex"):
        rules.append(
            f"z-index: {int(_as_float(layer.get('zIndex'), 0, -9999, 9999))} !important;"
        )
    if layer.get("visible") != default_layer.get("visible") and not bool(
        layer.get("visible", True)
    ):
        rules.append("display: none !important;")
    for property_name, state_key, default, minimum, maximum in (
        ("width", "width", 0, 0, 3000),
        ("height", "height", 0, 0, 3000),
    ):
        if layer.get(state_key) != default_layer.get(state_key):
            rules.append(
                f"{property_name}: {_number(layer.get(state_key), default, minimum, maximum)}px !important;"
            )
    background = _css_color(layer.get("backgroundColor"))
    if background and layer.get("backgroundColor") != default_layer.get(
        "backgroundColor"
    ):
        rules.append(f"background-color: {background} !important;")
    border_radius = layer.get("borderRadius")
    if border_radius != default_layer.get("borderRadius"):
        rules.append(
            f"border-radius: {_number(border_radius, 0, 0, 1000)}px !important;"
        )
    image_filter = str(layer.get("filter") or "none")
    if image_filter != default_layer.get("filter") and image_filter in {
        "none",
        "brightness(.85)",
        "brightness(1.15)",
        "grayscale(1)",
        "saturate(1.4)",
        "sepia(.5)",
    }:
        rules.append(f"filter: {image_filter} !important;")
    shadow = str(layer.get("boxShadow") or "").strip()
    if (
        shadow
        and shadow != default_layer.get("boxShadow")
        and SAFE_SHADOW_RE.fullmatch(shadow)
    ):
        rules.append(f"box-shadow: {shadow} !important;")
    if kind == "text":
        if layer.get("fontSize") != default_layer.get("fontSize"):
            rules.append(
                f"font-size: {_number(layer.get('fontSize'), 20, 1, 300)}px !important;"
            )
        family = _css_token(layer.get("fontFamily"), "cr105Font")
        if layer.get("fontFamily") != default_layer.get("fontFamily"):
            rules.append(f"font-family: '{family}' !important;")
        weight = _css_token(layer.get("fontWeight"), "normal")
        if layer.get("fontWeight") != default_layer.get("fontWeight") and weight in {
            "normal",
            "bold",
            "600",
            "700",
            "800",
        }:
            rules.append(f"font-weight: {weight} !important;")
        if layer.get("lineHeight") != default_layer.get("lineHeight"):
            rules.append(
                f"line-height: {_number(layer.get('lineHeight'), 1.1, 0.2, 5)} !important;"
            )
        if layer.get("letterSpacing") != default_layer.get("letterSpacing"):
            rules.append(
                f"letter-spacing: {_number(layer.get('letterSpacing'), 0, -20, 100)}px !important;"
            )
        text_align = str(layer.get("textAlign") or "left")
        if layer.get("textAlign") != default_layer.get("textAlign") and text_align in {
            "left",
            "center",
            "right",
        }:
            rules.append(f"text-align: {text_align} !important;")
        color = _css_color(layer.get("color"))
        if color and layer.get("color") != default_layer.get("color"):
            rules.append(f"color: {color} !important;")
    if kind == "image":
        object_fit = str(layer.get("objectFit") or "contain")
        if layer.get("objectFit") != default_layer.get("objectFit") and object_fit in {
            "contain",
            "cover",
            "fill",
            "none",
        }:
            rules.append(f"object-fit: {object_fit} !important;")
    avatar_rule = ""
    if layer_id == "avatar" and (
        layer.get("width") != default_layer.get("width")
        or layer.get("height") != default_layer.get("height")
    ):
        rules.append("overflow: hidden !important;")
        rules.append("flex-shrink: 0 !important;")
        avatar_rule = (
            f".avatar-img {{ width: {_number(layer.get('width'), 120, 1, 1000)}px !important; "
            f"height: {_number(layer.get('height'), 120, 1, 1000)}px !important; }}"
        )
    if not rules:
        return ""
    main_rule = f"{selector} {{ {' '.join(rules)} }}"
    return f"{main_rule}\n{avatar_rule}" if avatar_rule else main_rule


def _build_override_css(state: dict[str, Any]) -> str:
    theme = state.get("theme", {})
    theme_css = _theme_style(theme)
    default_layers = _load_default_state().get("layers", {})
    layer_css = [
        _layer_css(layer_id, layer, default_layers.get(layer_id, {}))
        for layer_id, layer in state.get("layers", {}).items()
    ]
    layer_css = [rule for rule in layer_css if rule]
    advanced_css = re.sub(
        r"</style",
        "",
        str(state.get("advancedCss") or ""),
        flags=re.IGNORECASE,
    )
    if len(advanced_css) > 30000:
        advanced_css = advanced_css[:30000]
    sections = [
        "/* Generated by the isolated template editor. */",
        theme_css,
        *layer_css,
    ]
    if advanced_css.strip():
        sections.extend(["/* User advanced CSS overrides. */", advanced_css])
    return "\n\n".join(sections)


def _custom_ref(state: dict[str, Any], layer: dict[str, Any]) -> Any:
    ref = layer.get("imageRef")
    if isinstance(ref, dict):
        return ref
    return state.get("assets", {}).get("overrides", {}).get(str(ref))


def _data_uri(ref: Any) -> str | None:
    path = _ref_path(ref)
    if not path:
        return None
    mime = str(ref.get("mime") or mimetypes.guess_type(path.name)[0] or "image/png")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _source_data_uri(relative: str) -> str:
    path = _safe_path(SOURCE_ASSET_ROOT, relative)
    if not path or not path.is_file():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _custom_layer_markup(state: dict[str, Any], generated: bool = False) -> str:
    markup: list[str] = []
    for layer in state.get("customLayers", []):
        if not isinstance(layer, dict) or not bool(layer.get("visible", True)):
            continue
        layer_id = str(layer.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", layer_id):
            continue
        kind = "image" if layer.get("kind") == "image" else "text"
        style = [
            "position:absolute",
            "left:0",
            "top:0",
            f"width:{_number(layer.get('width'), 180, 1, 3000)}px",
            f"height:{_number(layer.get('height'), 60, 1, 3000)}px",
            f"transform:{_layer_transform(layer)}",
            f"opacity:{_number(layer.get('opacity'), 1, 0, 1)}",
            f"z-index:{int(_as_float(layer.get('zIndex'), 1000, -9999, 9999))}",
            "transform-origin:center center",
            "box-sizing:border-box",
        ]
        background = _css_color(layer.get("backgroundColor"))
        if background:
            style.append(f"background-color:{background}")
        radius = _number(layer.get("borderRadius"), 0, 0, 1000)
        style.append(f"border-radius:{radius}px")
        shadow = str(layer.get("boxShadow") or "").strip()
        if shadow and SAFE_SHADOW_RE.fullmatch(shadow):
            style.append(f"box-shadow:{shadow}")
        if kind == "text":
            style.extend(
                [
                    f"font-size:{_number(layer.get('fontSize'), 28, 1, 300)}px",
                    f"font-family:'{_css_token(layer.get('fontFamily'), 'cr105Font')}'",
                    f"font-weight:{_css_token(layer.get('fontWeight'), 'normal')}",
                    f"line-height:{_number(layer.get('lineHeight'), 1.2, 0.2, 5)}",
                    f"letter-spacing:{_number(layer.get('letterSpacing'), 0, -20, 100)}px",
                    f"text-align:{str(layer.get('textAlign') or 'left') if str(layer.get('textAlign') or 'left') in {'left', 'center', 'right'} else 'left'}",
                    f"color:{_css_color(layer.get('color'), '#D47E8F')}",
                    "white-space:pre-wrap",
                    "overflow:hidden",
                ]
            )
            markup.append(
                f'<div class="editor-custom-layer" data-editor-layer="{html.escape(layer_id, quote=True)}" '
                f'style="{html.escape(";".join(style), quote=True)}">'
                f"{html.escape(str(layer.get('text') or ''))}</div>"
            )
            continue
        image_ref = _custom_ref(state, layer)
        image_url = _data_uri(image_ref) if generated else _uploaded_url(image_ref)
        image_url = image_url or (
            _source_data_uri("img/1.png") if generated else _source_url("img/1.png")
        )
        object_fit = str(layer.get("objectFit") or "contain")
        if object_fit not in {"contain", "cover", "fill", "none"}:
            object_fit = "contain"
        image_filter = str(layer.get("filter") or "none")
        if image_filter not in {
            "none",
            "brightness(.85)",
            "brightness(1.15)",
            "grayscale(1)",
            "saturate(1.4)",
            "sepia(.5)",
        }:
            image_filter = "none"
        style.extend(
            ["display:block", f"object-fit:{object_fit}", f"filter:{image_filter}"]
        )
        markup.append(
            f'<img class="editor-custom-layer" data-editor-layer="{html.escape(layer_id, quote=True)}" '
            f'src="{html.escape(image_url, quote=True)}" '
            f'style="{html.escape(";".join(style), quote=True)}" alt="">'
        )
    return "\n".join(markup)


def _inject_custom_layers(template: str, markup: str) -> str:
    if not markup:
        return template
    body_start = template.lower().rfind("</body>")
    if body_start < 0:
        return template
    before_body = template[:body_start]
    wrapper_close = before_body.rfind("</div>")
    if wrapper_close < 0:
        return template
    return (
        before_body[:wrapper_close]
        + markup
        + "\n"
        + before_body[wrapper_close:]
        + template[body_start:]
    )


def _render_state(state: dict[str, Any]) -> str:
    context = _build_template_data(state)
    assets = {
        "avatar_url": _asset_url(state, "avatar", "img/1.png"),
        "calendar": _asset_url(state, "calendar", "img/rl.png"),
        "main_character": _asset_url(state, "mainCharacter", "img/1.png"),
        "footer_character": _asset_url(state, "footerCharacter", "img/2.png"),
        "heart_empty": _asset_url(state, "heartEmpty", "img/h1.png"),
        "heart_full": _asset_url(state, "heartFull", "img/h2.png"),
        "tag": _asset_url(
            state,
            f"tag:{context['page']['tag_icon_name'].split('.', 1)[0]}",
            f"img/tag/{context['page']['tag_icon_name']}",
        ),
        "weather": _asset_url(
            state,
            f"weather:{context['page']['weather_icon_name'].split('.', 1)[0]}",
            f"img/weather/{context['page']['weather_icon_name']}",
        ),
    }
    font_rules = [
        f"@font-face {{ font-family: '{family}'; src: url('{_font_url(state, family)}') format('woff2'); }}"
        for family in FONT_FILES
    ]
    theme = state.get("theme", {})
    theme_style = _theme_style(theme)
    context["sign_style"] = "\n\n".join(
        [
            theme_style,
            "\n".join(font_rules),
            STYLE_PATH.read_text(encoding="utf-8"),
            FONT_FALLBACK_STYLE,
            _build_override_css(state),
        ]
    )
    context["assets"] = assets
    environment = Environment(
        loader=FileSystemLoader(PLUGIN_ROOT),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        cache_size=0,
    )
    rendered = environment.get_template(TEMPLATE_PATH.name).render(context)
    return _inject_custom_layers(rendered, _custom_layer_markup(state))


def _runtime_config(state: dict[str, Any], avatar_path: str | None) -> dict[str, Any]:
    content = state["content"]
    return {
        "brand_name": str(content.get("brandName") or "真寻"),
        "date_format": str(content.get("dateFormat") or "iso"),
        "avatar_url": avatar_path or str(content.get("avatarUrl") or ""),
        "messages": {
            "uid_prefix": str(content.get("uidPrefix") or "UID:"),
            "sign_count_prefix": str(content.get("signCountPrefix") or "累计签到"),
            "sign_count_suffix": str(content.get("signCountSuffix") or "天"),
            "sign_title": str(content.get("signTitle") or "今日签到"),
            "info_title": str(content.get("infoTitle") or "我的信息"),
            "bot_message_format": str(
                content.get("botMessageFormat") or "{brand_name}说: {message}"
            ),
            "day_message": str(content.get("dayMessage") or "{brand_name}希望你开心！"),
            "reward_primary": str(content.get("rewardPrimary") or "签到成功"),
            "reward_gold": str(content.get("rewardGold") or "金币 +{gold}"),
            "reward_item": str(content.get("rewardItem") or "{items}"),
            "info_primary": str(
                content.get("infoPrimary") or "累计签到：{sign_count} 天"
            ),
            "info_gold": str(content.get("infoGold") or "总金币：{gold_balance}"),
            "info_item": str(content.get("infoItem") or "上次签到：{last_sign_date}"),
            "none_item": str(content.get("noneItem") or "暂无道具"),
            "never_date": str(content.get("neverDate") or "还没有记录"),
            "success_status": str(content.get("successStatus") or "签到成功"),
            "already_status": str(content.get("alreadyStatus") or "今日已签到"),
            "not_signed_status": str(content.get("notSignedStatus") or "尚未签到"),
            "reserved_current_label": str(
                content.get("reservedCurrentLabel") or "当前好感度"
            ),
            "reserved_current": str(content.get("reservedCurrent") or "{favour}"),
            "reserved_level_label": str(
                content.get("reservedLevelLabel") or "好感度等级"
            ),
            "reserved_level_text": str(
                content.get("reservedLevelText") or "{favour_level}"
            ),
            "reserved_attitude": str(
                content.get("reservedAttitude") or "对你的态度: {favour_attitude}"
            ),
            "reserved_next_text": str(
                content.get("reservedNextText") or "距离升级还差{favour_next}好感度"
            ),
            "reserved_max_text": str(
                content.get("reservedMaxText") or "已达到最高好感等级"
            ),
            "reserved_separator": str(content.get("reservedSeparator") or ": "),
            "temperature_suffix": str(content.get("temperatureSuffix") or "℃"),
        },
        "morning_messages": [
            str(item) for item in content.get("morningMessages", []) if str(item)
        ],
        "late_night_messages": [
            str(item) for item in content.get("lateNightMessages", []) if str(item)
        ],
        "reserved_panel": {
            "current_label": str(content.get("reservedCurrentLabel") or "当前好感度"),
            "current": str(content.get("reservedCurrent") or "{favour}"),
            "level_label": str(content.get("reservedLevelLabel") or "好感度等级"),
            "level_text": str(content.get("reservedLevelText") or "{favour_level}"),
            "attitude": str(
                content.get("reservedAttitude") or "对你的态度: {favour_attitude}"
            ),
            "next_text": str(
                content.get("reservedNextText") or "距离升级还差{favour_next}好感度"
            ),
            "max_text": str(content.get("reservedMaxText") or "已达到最高好感等级"),
            "separator": str(content.get("reservedSeparator") or ": "),
            "progress": _as_float(content.get("reservedProgress"), 0, 0, 100),
            "heart_count": int(_as_float(content.get("heartCount"), 8, 0, 16)),
            "filled_hearts": int(_as_float(content.get("filledHearts"), 0, 0, 16)),
        },
    }


def _copy_override_assets(state: dict[str, Any], output_assets: Path) -> str | None:
    overrides = state.get("assets", {}).get("overrides", {})
    avatar_path = None
    for slot, target_relative in ASSET_TARGETS.items():
        path = _ref_path(overrides.get(slot))
        if not path:
            continue
        target = output_assets / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    avatar_ref = overrides.get("avatar")
    avatar_source = _ref_path(avatar_ref)
    if avatar_source:
        target = output_assets / "img" / "editor_avatar.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(avatar_source, target)
        avatar_path = "img/editor_avatar.png"
    return avatar_path


def _declared_pack_ids(install_root: Path) -> set[str]:
    pack_ids = {"default"}
    if not install_root.is_dir():
        return pack_ids
    for candidate in install_root.iterdir():
        try:
            if candidate.is_dir():
                manifest_path = candidate / "template.json"
                if not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            elif candidate.suffix.lower() == ".zip":
                with zipfile.ZipFile(candidate) as archive:
                    manifest_names = [
                        name
                        for name in archive.namelist()
                        if name.replace("\\", "/").rstrip("/").endswith("template.json")
                    ]
                    if len(manifest_names) != 1:
                        continue
                    manifest = json.loads(
                        archive.read(manifest_names[0]).decode("utf-8")
                    )
            else:
                continue
            if isinstance(manifest, dict) and manifest.get("id"):
                pack_ids.add(str(manifest["id"]).strip().lower())
        except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile):
            continue
    return pack_ids


def _new_pack_id(install_root: Path) -> str:
    existing = _declared_pack_ids(install_root)
    for _ in range(32):
        candidate = f"tpl_{uuid.uuid4().hex}"
        if (
            candidate not in existing
            and not (install_root / candidate).exists()
            and not (install_root / f"{candidate}.zip").exists()
        ):
            return candidate
    raise RuntimeError("无法生成不重复的模板内部标识")


def _template_name(state: dict[str, Any]) -> str:
    metadata = state.get("templateMeta", {})
    raw_name = metadata.get("name") if isinstance(metadata, dict) else ""
    fallback = state.get("content", {}).get("brandName", "自定义签到模板")
    name = " ".join(str(raw_name or fallback).split())
    return name[:80] or "自定义签到模板"


def _template_manifest(
    state: dict[str, Any],
    pack_id: str,
    avatar_asset: str | None,
) -> dict[str, Any]:
    metadata = state.get("templateMeta", {})
    if not isinstance(metadata, dict):
        metadata = {}
    settings = _runtime_config(state, avatar_path=None)
    if avatar_asset:
        settings["avatar_asset"] = avatar_asset
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "id": pack_id,
        "name": _template_name(state),
        "description": str(metadata.get("description") or ""),
        "card": {"width": 465, "height": 926},
        "files": {
            "template": "sign_card.html",
            "style": "sign_card.css",
            "assets": "assets/sign",
        },
        "settings": settings,
    }


def _generate_bundle(
    state: dict[str, Any],
    output_root: Path = OUTPUT_ROOT,
    install_root: Path = TEMPLATE_PACK_ROOT,
) -> dict[str, Any]:
    state = _normalise_state(state)
    output_root.mkdir(parents=True, exist_ok=True)
    install_root.mkdir(parents=True, exist_ok=True)
    pack_id = _new_pack_id(install_root)
    pack_name = _template_name(state)
    output_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    output_dir = output_root / output_id
    output_dir.mkdir(parents=True, exist_ok=False)
    output_assets = output_dir / "assets" / "sign"
    shutil.copytree(SOURCE_ASSET_ROOT, output_assets)

    avatar_asset = _copy_override_assets(state, output_assets)
    template = _inject_custom_layers(
        TEMPLATE_PATH.read_text(encoding="utf-8"),
        _custom_layer_markup(state, generated=True),
    )
    (output_dir / "sign_card.html").write_text(template, encoding="utf-8")
    css = (
        STYLE_PATH.read_text(encoding="utf-8")
        + "\n\n"
        + FONT_FALLBACK_STYLE
        + "\n\n"
        + _build_override_css(state)
        + "\n"
    )
    (output_dir / "sign_card.css").write_text(css, encoding="utf-8")
    shutil.copy2(MANIFEST_PATH, output_dir / "sign_card.manifest.json")
    manifest = _template_manifest(state, pack_id, avatar_asset)
    (output_dir / "template.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "editor_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        f"# {pack_name} 签到模板\n\n"
        "这是由 `template_editor` 生成的自包含签到模板包。"
        "文字、样式和素材均保存在本目录内。\n\n"
        "## 安装\n\n"
        "1. 保持本目录中的 `template.json`、`sign_card.html`、"
        "`sign_card.css` 和 `assets/sign` 结构不变。\n"
        "2. 推荐在 AstrBot WebUI 打开 `插件管理 -> 真寻签到 -> 插件配置`，"
        "在“签到模板管理”上传同名 ZIP，然后点击配置页底部的“保存”。\n"
        "3. 也可以将整个文件夹或 ZIP 复制到 `zhenxun_astr/template_packs`，"
        "ZIP 无需解压。\n"
        "4. 设置页上传默认会自动应用新模板；关闭自动应用时，请在插件重载后从“当前模板”下拉框选择。\n"
        f"5. 直接复制文件时，重载插件后选择“{pack_name}”并保存，再发送签到命令验证。\n\n"
        "## 说明\n\n"
        "- 内部随机 ID 只用于唯一识别和保存选择状态，不显示在设置下拉框。\n"
        "- 无需合并或替换 AstrBot 插件配置。\n"
        "- 好感度占位符由真寻签到插件的可选联动模块在运行时填充。\n"
        "- `editor_state.json` 仅用于重新导入编辑器继续修改。\n",
        encoding="utf-8",
    )

    installed_dir = install_root / pack_id
    temporary_dir = install_root / f".{pack_id}-{uuid.uuid4().hex[:8]}.tmp"
    shutil.copytree(output_dir, temporary_dir)
    temporary_dir.replace(installed_dir)

    zip_path = output_root / f"{output_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in output_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(output_dir).as_posix())
    return {
        "output_id": output_id,
        "output_dir": output_dir,
        "zip_path": zip_path,
        "pack_id": pack_id,
        "pack_name": pack_name,
        "installed_dir": installed_dir,
    }


def _project_payload(state: dict[str, Any]) -> dict[str, Any]:
    embedded: dict[str, Any] = {}
    refs: list[Any] = []
    overrides = state.get("assets", {}).get("overrides", {})
    refs.extend(overrides.values())
    for layer in state.get("customLayers", []):
        if isinstance(layer, dict) and isinstance(layer.get("imageRef"), dict):
            refs.append(layer["imageRef"])
    for ref in refs:
        path = _ref_path(ref)
        if not path or path.name in embedded:
            continue
        embedded[path.name] = {
            "name": str(ref.get("name") or path.name),
            "mime": str(ref.get("mime") or "application/octet-stream"),
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    return {
        "format": "zhenxun-template-editor-project",
        "version": 1,
        "state": state,
        "assets": embedded,
    }


def _replace_project_refs(value: Any, mapping: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        result = {
            key: _replace_project_refs(item, mapping) for key, item in value.items()
        }
        old_file = result.get("file")
        if isinstance(old_file, str) and old_file in mapping:
            result["file"] = mapping[old_file]["file"]
            result["url"] = mapping[old_file]["url"]
        return result
    if isinstance(value, list):
        return [_replace_project_refs(item, mapping) for item in value]
    return value


def _import_project(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("format") != "zhenxun-template-editor-project":
        raise ValueError("不是签到模板编辑器项目文件")
    state = _normalise_state(payload.get("state"))
    assets = payload.get("assets", {})
    mapping: dict[str, dict[str, Any]] = {}
    if isinstance(assets, dict):
        for old_name, item in assets.items():
            if not isinstance(item, dict):
                continue
            raw = base64.b64decode(str(item.get("data") or ""), validate=True)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError("项目中的素材超过大小限制")
            suffix = ".woff2" if str(item.get("mime")) == "font/woff2" else ".png"
            filename = f"import-{uuid.uuid4().hex}{suffix}"
            (UPLOAD_ROOT / filename).write_bytes(raw)
            mapping[str(old_name)] = {
                "file": filename,
                "url": f"/uploaded-assets/{quote(filename)}",
                "name": str(item.get("name") or filename),
                "mime": str(item.get("mime") or "image/png"),
            }
    return _replace_project_refs(state, mapping)


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "zhenxun-template-editor/1.0"

    def do_GET(self) -> None:
        request = urlparse(self.path)
        path = request.path
        if path in {"/", "/index.html"}:
            self._serve_file(EDITOR_ROOT / "index.html", "text/html; charset=utf-8")
        elif path == "/editor.css":
            self._serve_file(EDITOR_ROOT / "editor.css", "text/css; charset=utf-8")
        elif path == "/editor.js":
            self._serve_file(
                EDITOR_ROOT / "editor.js", "text/javascript; charset=utf-8"
            )
        elif path == "/api/default-state":
            self._send_json(_load_default_state())
        elif path.startswith("/source-assets/"):
            self._serve_rooted_file(SOURCE_ASSET_ROOT, path, "/source-assets/")
        elif path.startswith("/uploaded-assets/"):
            self._serve_rooted_file(UPLOAD_ROOT, path, "/uploaded-assets/")
        elif path.startswith("/generated/"):
            self._serve_rooted_file(OUTPUT_ROOT, path, "/generated/")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        request = urlparse(self.path)
        try:
            payload = self._read_json()
            if request.path == "/api/render":
                state = _normalise_state(payload)
                self._send_json({"html": _render_state(state)})
            elif request.path == "/api/upload":
                self._send_json({"override": self._save_upload(payload)})
            elif request.path == "/api/generate":
                state = _normalise_state(payload)
                generated = _generate_bundle(state)
                self._send_json(
                    {
                        "id": generated["output_id"],
                        "pack_id": generated["pack_id"],
                        "pack_name": generated["pack_name"],
                        "download": (f"/generated/{quote(generated['zip_path'].name)}"),
                        "directory": str(generated["output_dir"].resolve()),
                        "installed": str(generated["installed_dir"].resolve()),
                    }
                )
            elif request.path == "/api/project/export":
                self._send_json(_project_payload(_normalise_state(payload)))
            elif request.path == "/api/project/import":
                self._send_json({"state": _import_project(payload)})
            else:
                self.send_error(404)
        except Exception as error:
            self._send_json({"error": str(error)}, status=400)

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("请求长度无效") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("请求体为空或超过大小限制")
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _save_upload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("素材请求格式无效")
        slot = str(payload.get("slot") or "")
        is_custom = slot.startswith("custom:") and bool(
            re.fullmatch(r"custom:[a-zA-Z0-9_-]+", slot)
        )
        if slot not in ASSET_TARGETS and not is_custom:
            raise ValueError("未知素材槽位")
        data_url = str(payload.get("data") or "")
        if "," not in data_url:
            raise ValueError("素材数据必须是 data URL")
        _, encoded = data_url.split(",", 1)
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("素材为空或超过 16MB 限制")
        if slot.startswith("font:"):
            if raw[:4] != b"wOF2":
                raise ValueError("字体暂时只接受 WOFF2 文件")
            filename = f"font-{uuid.uuid4().hex}.woff2"
            target = UPLOAD_ROOT / filename
            target.write_bytes(raw)
            normalized_mime = "font/woff2"
        else:
            try:
                with Image.open(BytesIO(raw)) as source:
                    if source.width > 4096 or source.height > 4096:
                        raise ValueError("图片宽高不能超过 4096px")
                    image = ImageOps.exif_transpose(source).convert("RGBA")
                    output = BytesIO()
                    image.save(output, format="PNG", optimize=True)
                    image.close()
            except ValueError:
                raise
            except Exception as error:
                raise ValueError(
                    "无法读取图片，请使用 PNG、JPG、WEBP 或 GIF"
                ) from error
            filename = f"image-{uuid.uuid4().hex}.png"
            target = UPLOAD_ROOT / filename
            target.write_bytes(output.getvalue())
            normalized_mime = "image/png"
        return {
            "file": filename,
            "url": f"/uploaded-assets/{quote(filename)}",
            "name": str(payload.get("name") or filename),
            "mime": normalized_mime,
            "slot": slot,
            "bytes": target.stat().st_size,
        }

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        self._send_bytes(path.read_bytes(), content_type)

    def _serve_rooted_file(self, root: Path, request_path: str, prefix: str) -> None:
        relative = unquote(request_path.removeprefix(prefix))
        path = _safe_path(root, relative)
        if not path or not path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send_bytes(path.read_bytes(), content_type)

    def _send_json(self, value: Any, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stderr.write(
            f"{self.address_string()} - - [{self.log_date_time_string()}] "
            f"{format_string % args}\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the isolated sign template editor"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    _ensure_workspace()
    server = ThreadingHTTPServer((args.host, args.port), EditorHandler)
    print(f"签到模板编辑器：http://{args.host}:{args.port}/", flush=True)
    print(
        "生成包会自动安装到 template_packs，并保存在 template_editor/output。",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
