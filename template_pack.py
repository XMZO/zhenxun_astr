from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PACK_FORMAT = "zhenxun-astr-template"
PACK_VERSION = 1
DEFAULT_TEMPLATE_FILE = "sign_card.html"
DEFAULT_STYLE_FILE = "sign_card.css"
DEFAULT_ASSET_ROOT = "assets/sign"
DEFAULT_CARD_WIDTH = 465
DEFAULT_CARD_HEIGHT = 926
MAX_ARCHIVE_FILES = 256
MAX_ARCHIVE_FILE_BYTES = 24 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 96 * 1024 * 1024
PACK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TemplatePackError(ValueError):
    """Raised when a template pack is invalid or unsafe."""


def _normalise_relative(value: Any, *, allow_empty: bool = False) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        if allow_empty:
            return ""
        raise TemplatePackError("模板包路径不能为空")
    if text.startswith("/") or re.match(r"^[a-zA-Z]:", text):
        raise TemplatePackError(f"模板包路径必须是相对路径: {text}")
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise TemplatePackError(f"模板包路径不安全: {text}")
    return path.as_posix().rstrip("/")


def _join_relative(base: str, relative: str) -> str:
    return _normalise_relative(f"{base}/{relative}" if base else relative)


def _safe_pack_id(value: str) -> str:
    pack_id = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if not pack_id:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        pack_id = f"template-{digest}"
    if pack_id == "default":
        pack_id = "default-custom"
    return pack_id[:64].rstrip("-_")


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", value)
    )


@dataclass(frozen=True)
class TemplatePack:
    id: str
    legacy_ids: tuple[str, ...]
    name: str
    description: str
    source_path: Path
    source_kind: str
    root_prefix: str
    template_file: str
    style_file: str
    asset_root: str
    settings: dict[str, Any]
    width: int
    height: int
    fingerprint: str
    file_names: tuple[str, ...]

    def _source_name(self, relative: str) -> str:
        logical_name = _normalise_relative(relative)
        return (
            _join_relative(self.root_prefix, logical_name)
            if self.root_prefix
            else logical_name
        )

    def read_bytes(self, relative: str) -> bytes:
        source_name = self._source_name(relative)
        if source_name not in self.file_names:
            raise TemplatePackError(f"模板包 {self.id} 缺少文件: {relative}")
        if self.source_kind in {"folder", "legacy"}:
            root = self.source_path.resolve()
            target = (self.source_path / source_name).resolve()
            if not target.is_relative_to(root):
                raise TemplatePackError(f"模板包路径越界: {relative}")
            return target.read_bytes()
        if self.source_kind == "zip":
            with zipfile.ZipFile(self.source_path) as archive:
                return archive.read(source_name)
        raise TemplatePackError(f"未知模板包类型: {self.source_kind}")

    def read_many(self, relatives: list[str] | tuple[str, ...]) -> dict[str, bytes]:
        requested = {
            _normalise_relative(relative): self._source_name(relative)
            for relative in relatives
        }
        missing = [name for name in requested.values() if name not in self.file_names]
        if missing:
            raise TemplatePackError(f"模板包 {self.id} 缺少文件: {missing[0]}")
        if self.source_kind == "zip":
            with zipfile.ZipFile(self.source_path) as archive:
                return {
                    logical_name: archive.read(source_name)
                    for logical_name, source_name in requested.items()
                }
        return {
            logical_name: self.read_bytes(logical_name) for logical_name in requested
        }

    def read_text(self, relative: str) -> str:
        return self.read_bytes(relative).decode("utf-8")

    def exists(self, relative: str) -> bool:
        try:
            source_name = self._source_name(relative)
        except TemplatePackError:
            return False
        return source_name in self.file_names

    def asset_path(self, relative: str) -> str:
        return _join_relative(self.asset_root, relative)

    def _asset_names(self, directory: str) -> tuple[str, ...]:
        prefix = self._source_name(self.asset_path(directory)) + "/"
        names = {
            PurePosixPath(name).name
            for name in self.file_names
            if name.startswith(prefix)
            and "/" not in name[len(prefix) :]
            and PurePosixPath(name).suffix.lower()
            in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        }
        return tuple(sorted(names, key=_natural_key))

    @property
    def tag_names(self) -> tuple[str, ...]:
        return self._asset_names("img/tag")

    @property
    def weather_names(self) -> tuple[str, ...]:
        return self._asset_names("img/weather")


@dataclass(frozen=True)
class TemplateInstallResult:
    pack: TemplatePack
    target_path: Path
    action: str


class TemplateRegistry:
    def __init__(self, plugin_root: Path, persistent_root: Path) -> None:
        self.plugin_root = plugin_root
        self.bundled_root = plugin_root / "template_packs"
        self.persistent_root = persistent_root

    def discover(self) -> tuple[dict[str, TemplatePack], list[str]]:
        packs = {"default": self.legacy_pack()}
        errors: list[str] = []
        self.persistent_root.mkdir(parents=True, exist_ok=True)
        for root in (self.bundled_root, self.persistent_root):
            if not root.is_dir():
                continue
            for candidate in sorted(root.iterdir(), key=lambda path: path.name.lower()):
                if candidate.name.startswith("."):
                    continue
                if not candidate.is_dir() and candidate.suffix.lower() != ".zip":
                    continue
                try:
                    pack = self.load(candidate)
                    if pack.id == "default":
                        raise TemplatePackError("default 是内置模板保留 ID")
                    packs[pack.id] = pack
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    zipfile.BadZipFile,
                    TemplatePackError,
                ) as error:
                    errors.append(f"{candidate.name}: {error}")
        return packs, errors

    def legacy_pack(self) -> TemplatePack:
        file_names = self._legacy_file_names()
        required = {DEFAULT_TEMPLATE_FILE, DEFAULT_STYLE_FILE}
        if not required.issubset(file_names):
            raise TemplatePackError("内置默认签到模板文件不完整")
        return TemplatePack(
            id="default",
            legacy_ids=(),
            name="官方默认模板",
            description="插件内置的真寻官方签到模板",
            source_path=self.plugin_root,
            source_kind="legacy",
            root_prefix="",
            template_file=DEFAULT_TEMPLATE_FILE,
            style_file=DEFAULT_STYLE_FILE,
            asset_root=DEFAULT_ASSET_ROOT,
            settings={},
            width=DEFAULT_CARD_WIDTH,
            height=DEFAULT_CARD_HEIGHT,
            fingerprint=self._folder_fingerprint(self.plugin_root, file_names),
            file_names=file_names,
        )

    def _legacy_file_names(self) -> tuple[str, ...]:
        names = [DEFAULT_TEMPLATE_FILE, DEFAULT_STYLE_FILE]
        asset_root = self.plugin_root / DEFAULT_ASSET_ROOT
        if asset_root.is_dir():
            names.extend(
                file_path.relative_to(self.plugin_root).as_posix()
                for file_path in asset_root.rglob("*")
                if file_path.is_file()
            )
        return tuple(sorted(names))

    def load(self, path: Path) -> TemplatePack:
        if path.is_dir():
            return self._load_folder(path)
        if path.suffix.lower() == ".zip":
            return self._load_zip(path)
        raise TemplatePackError("模板必须是文件夹或 ZIP")

    def install_zip(self, source_path: Path) -> TemplateInstallResult:
        source_path = source_path.resolve()
        if not source_path.is_file() or source_path.suffix.lower() != ".zip":
            raise TemplatePackError("上传源必须是可读取的 ZIP 文件")

        try:
            source_pack = self.load(source_path)
        except TemplatePackError:
            raise
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ) as error:
            raise TemplatePackError(f"无法读取模板 ZIP: {error}") from error
        if source_pack.id == "default":
            raise TemplatePackError("default 是内置模板保留 ID")

        self.persistent_root.mkdir(parents=True, exist_ok=True)
        target_path = self.persistent_root / f"{source_pack.id}.zip"
        if target_path.exists() and not target_path.is_file():
            raise TemplatePackError(f"安装目标不是文件: {target_path.name}")

        source_digest = self._file_digest(source_path)
        if target_path.is_file() and source_digest == self._file_digest(target_path):
            return TemplateInstallResult(
                pack=self.load(target_path),
                target_path=target_path,
                action="unchanged",
            )

        action = "updated" if target_path.is_file() else "installed"
        temporary_path = self.persistent_root / (
            f".{source_pack.id}-{uuid.uuid4().hex[:12]}.zip"
        )
        try:
            shutil.copyfile(source_path, temporary_path)
            copied_pack = self.load(temporary_path)
            if copied_pack.id != source_pack.id:
                raise TemplatePackError("模板 ZIP 在安装过程中发生变化")
            if self._file_digest(temporary_path) != source_digest:
                raise TemplatePackError("模板 ZIP 复制校验失败")
            temporary_path.replace(target_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        return TemplateInstallResult(
            pack=self.load(target_path),
            target_path=target_path,
            action=action,
        )

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _load_folder(self, path: Path) -> TemplatePack:
        file_names = self._folder_file_names(path)
        manifest = self._read_folder_manifest(path, file_names)
        return self._build_pack(
            manifest=manifest,
            fallback_id=path.name,
            source_path=path,
            source_kind="folder",
            root_prefix="",
            file_names=file_names,
            fingerprint=self._folder_fingerprint(path, file_names),
        )

    def _load_zip(self, path: Path) -> TemplatePack:
        with zipfile.ZipFile(path) as archive:
            file_names = self._validate_archive(archive)
            root_prefix = self._archive_root(file_names)
            manifest_name = (
                _join_relative(root_prefix, "template.json")
                if root_prefix
                else "template.json"
            )
            if manifest_name in file_names:
                manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
            else:
                manifest = {}
        stat = path.stat()
        fingerprint = (
            f"zip:{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{root_prefix}"
        )
        return self._build_pack(
            manifest=manifest,
            fallback_id=path.stem,
            source_path=path,
            source_kind="zip",
            root_prefix=root_prefix,
            file_names=file_names,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _folder_file_names(root: Path) -> tuple[str, ...]:
        root_resolved = root.resolve()
        names: list[str] = []
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            resolved = file_path.resolve()
            if not resolved.is_relative_to(root_resolved):
                raise TemplatePackError(f"模板包包含越界链接: {file_path.name}")
            names.append(file_path.relative_to(root).as_posix())
        return tuple(sorted(names))

    @staticmethod
    def _folder_fingerprint(root: Path, file_names: tuple[str, ...]) -> str:
        digest = hashlib.sha256()
        digest.update(str(root.resolve()).encode("utf-8"))
        for name in file_names:
            stat = (root / name).stat()
            digest.update(name.encode("utf-8"))
            digest.update(f":{stat.st_mtime_ns}:{stat.st_size}".encode("ascii"))
        return f"folder:{digest.hexdigest()}"

    @staticmethod
    def _read_folder_manifest(
        root: Path, file_names: tuple[str, ...]
    ) -> dict[str, Any]:
        if "template.json" not in file_names:
            return {}
        return json.loads((root / "template.json").read_text(encoding="utf-8"))

    @staticmethod
    def _validate_archive(archive: zipfile.ZipFile) -> tuple[str, ...]:
        names: list[str] = []
        total_size = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            if len(names) >= MAX_ARCHIVE_FILES:
                raise TemplatePackError(f"ZIP 文件数超过 {MAX_ARCHIVE_FILES}")
            name = _normalise_relative(info.filename)
            if name in names:
                raise TemplatePackError(f"ZIP 包含重复路径: {name}")
            if info.flag_bits & 0x1:
                raise TemplatePackError("不支持加密 ZIP")
            if info.file_size > MAX_ARCHIVE_FILE_BYTES:
                raise TemplatePackError(f"ZIP 单文件超过限制: {name}")
            total_size += info.file_size
            if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                raise TemplatePackError("ZIP 解压后总体积超过限制")
            names.append(name)
        return tuple(sorted(names))

    @staticmethod
    def _archive_root(file_names: tuple[str, ...]) -> str:
        names = set(file_names)
        if "template.json" in names or DEFAULT_TEMPLATE_FILE in names:
            return ""
        candidates = {
            name.split("/", 1)[0]
            for name in names
            if "/" in name
            and name.split("/", 1)[1] in {"template.json", DEFAULT_TEMPLATE_FILE}
        }
        if len(candidates) != 1:
            raise TemplatePackError("ZIP 根目录中未找到唯一模板")
        return candidates.pop()

    @staticmethod
    def _manifest_value(
        manifest: dict[str, Any],
        key: str,
        default: Any,
        validator: Callable[[Any], bool] | None = None,
    ) -> Any:
        value = manifest.get(key, default)
        if validator is not None and not validator(value):
            raise TemplatePackError(f"template.json 字段无效: {key}")
        return value

    def _build_pack(
        self,
        *,
        manifest: dict[str, Any],
        fallback_id: str,
        source_path: Path,
        source_kind: str,
        root_prefix: str,
        file_names: tuple[str, ...],
        fingerprint: str,
    ) -> TemplatePack:
        if not isinstance(manifest, dict):
            raise TemplatePackError("template.json 顶层必须是对象")
        if manifest:
            if manifest.get("format") != PACK_FORMAT:
                raise TemplatePackError(f"template.json format 必须是 {PACK_FORMAT}")
            if manifest.get("version") != PACK_VERSION:
                raise TemplatePackError(f"暂不支持模板版本: {manifest.get('version')}")

        pack_id = str(manifest.get("id") or _safe_pack_id(fallback_id)).lower()
        if not PACK_ID_PATTERN.fullmatch(pack_id):
            raise TemplatePackError(
                "模板 ID 仅支持小写字母、数字、下划线和连字符，最长 64 位"
            )
        raw_legacy_ids = manifest.get("legacy_ids", [])
        if not isinstance(raw_legacy_ids, list):
            raise TemplatePackError("template.json legacy_ids 必须是数组")
        legacy_ids: list[str] = []
        for raw_legacy_id in raw_legacy_ids:
            legacy_id = str(raw_legacy_id).strip().lower()
            if not PACK_ID_PATTERN.fullmatch(legacy_id):
                raise TemplatePackError("template.json 包含无效的 legacy_ids")
            if legacy_id != pack_id and legacy_id not in legacy_ids:
                legacy_ids.append(legacy_id)
        name = str(manifest.get("name") or fallback_id).strip() or pack_id
        description = str(manifest.get("description") or "").strip()

        files = manifest.get("files", {})
        if not isinstance(files, dict):
            raise TemplatePackError("template.json files 必须是对象")
        template_file = _normalise_relative(
            files.get("template", DEFAULT_TEMPLATE_FILE)
        )
        style_file = _normalise_relative(files.get("style", DEFAULT_STYLE_FILE))
        asset_root = _normalise_relative(files.get("assets", DEFAULT_ASSET_ROOT))

        card = manifest.get("card", {})
        if not isinstance(card, dict):
            raise TemplatePackError("template.json card 必须是对象")
        width = self._card_dimension(card.get("width", DEFAULT_CARD_WIDTH), "width")
        height = self._card_dimension(card.get("height", DEFAULT_CARD_HEIGHT), "height")
        settings = manifest.get("settings", {})
        if not isinstance(settings, dict):
            raise TemplatePackError("template.json settings 必须是对象")

        def rooted(value: str) -> str:
            return _join_relative(root_prefix, value) if root_prefix else value

        for required in (template_file, style_file):
            if rooted(required) not in file_names:
                raise TemplatePackError(f"模板包缺少文件: {required}")
        asset_prefix = rooted(asset_root) + "/"
        if not any(name.startswith(asset_prefix) for name in file_names):
            raise TemplatePackError(f"模板包缺少素材目录: {asset_root}")

        return TemplatePack(
            id=pack_id,
            legacy_ids=tuple(legacy_ids),
            name=name,
            description=description,
            source_path=source_path,
            source_kind=source_kind,
            root_prefix=root_prefix,
            template_file=template_file,
            style_file=style_file,
            asset_root=asset_root,
            settings=json.loads(json.dumps(settings, ensure_ascii=False)),
            width=width,
            height=height,
            fingerprint=f"{fingerprint}:{pack_id}",
            file_names=file_names,
        )

    @staticmethod
    def _card_dimension(value: Any, key: str) -> int:
        if isinstance(value, bool):
            raise TemplatePackError(f"卡片尺寸无效: {key}")
        try:
            result = int(value)
        except (TypeError, ValueError) as error:
            raise TemplatePackError(f"卡片尺寸无效: {key}") from error
        if not 64 <= result <= 4096:
            raise TemplatePackError(f"卡片尺寸必须在 64 至 4096 之间: {key}")
        return result
