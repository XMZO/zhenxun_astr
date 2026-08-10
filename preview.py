from __future__ import annotations

import argparse
import copy
import html
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
except ImportError as error:
    raise SystemExit(
        "缺少 Jinja2，请使用 `uv run --with jinja2 preview.py` 启动预览。"
    ) from error


ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT / "sign_card.html"
STYLE_PATH = ROOT / "sign_card.css"
DATA_PATH = ROOT / "preview_data.json"
ASSET_ROOT = ROOT / "assets" / "sign"

FONT_FILES = {
    "cr105Font": "ChillReunion_105S.woff2",
    "cr65sFont": "ChillReunion_65S.woff2",
    "shFont": "SourceHanSansSC-Bold.woff2",
    "rxxxtFont": "rxxxkat.woff2",
    "kcytFont": "jcyt.woff2",
}

THEME_STYLE = """
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


def _asset_url(path: Path) -> str:
    relative_path = path.resolve().relative_to(ASSET_ROOT.resolve()).as_posix()
    version = path.stat().st_mtime_ns
    return f"/assets/{quote(relative_path, safe='/')}?v={version}"


def _read_preview_data() -> dict[str, Any]:
    with DATA_PATH.open("r", encoding="utf-8") as data_file:
        data = json.load(data_file)
    if not isinstance(data, dict):
        raise ValueError("preview_data.json 的顶层必须是对象")
    return copy.deepcopy(data)


def _query_value(query: dict[str, list[str]], name: str, default: str) -> str:
    values = query.get(name)
    return values[0] if values else default


def _query_int(
    query: dict[str, list[str]],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(_query_value(query, name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _icon_index(value: Any, maximum: int) -> int:
    try:
        index = int(str(value).split(".", 1)[0])
    except ValueError:
        return 0
    return min(maximum, max(0, index))


def _build_style() -> str:
    font_rules = []
    for family_name, file_name in FONT_FILES.items():
        font_path = ASSET_ROOT / "fonts" / file_name
        font_rules.append(
            f'@font-face {{ font-family: "{family_name}"; '
            f'src: url("{_asset_url(font_path)}") format("woff2"); }}'
        )
    return "\n\n".join(
        [THEME_STYLE, "\n".join(font_rules), STYLE_PATH.read_text(encoding="utf-8")]
    )


def _build_context(query: dict[str, list[str]] | None = None) -> dict[str, Any]:
    query = query or {}
    data = _read_preview_data()
    user = data.setdefault("user", {})
    page = data.setdefault("page", {})

    user["nickname"] = _query_value(
        query, "name", str(user.get("nickname", "预览用户"))
    )
    user["font_size"] = 27 if len(user["nickname"]) > 6 else 45
    page["temperature"] = _query_int(
        query, "temperature", int(page.get("temperature", 23)), -50, 60
    )
    weather_index = _query_int(
        query,
        "weather",
        _icon_index(page.get("weather_icon_name", "0.png"), 11),
        0,
        11,
    )
    tag_index = _query_int(
        query,
        "tag",
        _icon_index(page.get("tag_icon_name", "0.png"), 5),
        0,
        5,
    )
    page["weather_icon_name"] = f"{weather_index}.png"
    page["tag_icon_name"] = f"{tag_index}.png"

    mode = _query_value(query, "mode", "sign")
    data["is_card_view"] = mode == "view"
    data["mode"] = mode
    data["sign_style"] = _build_style()

    user_avatar = str(user.get("avatar_file", "img/1.png"))
    avatar_path = (ASSET_ROOT / user_avatar).resolve()
    if not avatar_path.is_file() or ASSET_ROOT.resolve() not in avatar_path.parents:
        avatar_path = ASSET_ROOT / "img" / "1.png"

    data["assets"] = {
        "avatar_url": _asset_url(avatar_path),
        "calendar": _asset_url(ASSET_ROOT / "img" / "rl.png"),
        "main_character": _asset_url(ASSET_ROOT / "img" / "1.png"),
        "footer_character": _asset_url(ASSET_ROOT / "img" / "2.png"),
        "heart_empty": _asset_url(ASSET_ROOT / "img" / "h1.png"),
        "heart_full": _asset_url(ASSET_ROOT / "img" / "h2.png"),
        "tag": _asset_url(ASSET_ROOT / "img" / "tag" / page["tag_icon_name"]),
        "weather": _asset_url(
            ASSET_ROOT / "img" / "weather" / page["weather_icon_name"]
        ),
    }
    return data


def render_preview(query: dict[str, list[str]] | None = None) -> str:
    environment = Environment(
        loader=FileSystemLoader(ROOT),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        cache_size=0,
    )
    return environment.get_template(TEMPLATE_PATH.name).render(_build_context(query))


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "zhenxun-sign-preview/1.0"

    def do_GET(self) -> None:
        request = urlparse(self.path)
        if request.path in {"/", "/index.html"}:
            self._serve_html(parse_qs(request.query))
            return
        if request.path.startswith("/assets/"):
            self._serve_asset(request.path)
            return
        if request.path == "/health":
            self._send_bytes(b"ok", "text/plain; charset=utf-8")
            return
        self.send_error(404)

    def _serve_html(self, query: dict[str, list[str]]) -> None:
        try:
            content = render_preview(query).encode("utf-8")
        except Exception as error:
            content = (
                "<h1>Preview error</h1><pre>" + html.escape(str(error)) + "</pre>"
            ).encode("utf-8")
            self._send_bytes(content, "text/html; charset=utf-8", status=500)
            return
        self._send_bytes(content, "text/html; charset=utf-8")

    def _serve_asset(self, request_path: str) -> None:
        relative_path = unquote(request_path.removeprefix("/assets/"))
        asset_path = (ASSET_ROOT / relative_path).resolve()
        asset_root = ASSET_ROOT.resolve()
        try:
            asset_path.relative_to(asset_root)
        except ValueError:
            self.send_error(404)
            return
        if not asset_path.is_file():
            self.send_error(404)
            return
        mime_type = (
            mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        )
        self._send_bytes(asset_path.read_bytes(), mime_type)

    def _send_bytes(
        self,
        content: bytes,
        content_type: str,
        status: int = 200,
    ) -> None:
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
        description="Serve a local zhenxun sign-card preview"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), PreviewHandler)
    print(f"签到卡片预览：http://{args.host}:{args.port}/", flush=True)
    print("修改 sign_card.html、sign_card.css 或素材后刷新浏览器即可。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
