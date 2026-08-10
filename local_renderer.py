from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


class LocalRenderUnavailable(RuntimeError):
    """Raised when the optional local browser renderer cannot be used."""


class LocalHtmlRenderer:
    """Render sign templates in a reused local Chromium instance."""

    _IMAGE_WAIT_SECONDS = 2.0
    _FONT_WAIT_SECONDS = 2.0
    _MAX_CONCURRENT_PAGES = 4

    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._start_lock = asyncio.Lock()
        self._template_lock = asyncio.Lock()
        self._render_semaphore = asyncio.Semaphore(self._MAX_CONCURRENT_PAGES)
        self._template_cache: dict[str, Any] = {}
        self._start_attempted = False
        self._failure: str | None = None

    @property
    def available(self) -> bool:
        browser = self._browser
        if browser is None or self._context is None:
            return False
        is_connected = getattr(browser, "is_connected", None)
        if callable(is_connected):
            try:
                return bool(is_connected())
            except Exception:
                return False
        return True

    async def start(self) -> bool:
        if self.available:
            return True
        if self._start_attempted:
            return False

        async with self._start_lock:
            if self.available:
                return True
            if self._start_attempted:
                return False
            self._start_attempted = True

            try:
                from playwright.async_api import async_playwright

                playwright = await async_playwright().start()
                browser = await self._launch_browser(playwright)
                context = await browser.new_context(
                    viewport={"width": 800, "height": 1000},
                    device_scale_factor=1,
                )
                page = await context.new_page()
                await page.goto("about:blank", wait_until="domcontentloaded")
                await page.close()
            except Exception as error:
                self._failure = str(error)
                with_context = locals().get("context")
                with_browser = locals().get("browser")
                with_playwright = locals().get("playwright")
                for resource in (with_context, with_browser):
                    close = getattr(resource, "close", None)
                    if callable(close):
                        try:
                            await close()
                        except Exception:
                            pass
                stop = getattr(with_playwright, "stop", None)
                if callable(stop):
                    try:
                        await stop()
                    except Exception:
                        pass
                self.logger.debug("Local sign renderer unavailable: %s", error)
                return False

            self._playwright = playwright
            self._browser = browser
            self._context = context
            self.logger.info("Local sign renderer is ready")
            return True

    async def render(
        self,
        template: str,
        data: dict[str, Any],
        *,
        width: int,
        height: int,
        template_key: str,
    ) -> str:
        if not await self.start():
            reason = self._failure or "browser is unavailable"
            raise LocalRenderUnavailable(reason)
        if not self.available:
            raise LocalRenderUnavailable("browser is disconnected")

        rendered_html = await self._render_template(template, data, template_key)
        async with self._render_semaphore:
            page = None
            try:
                page = await self._context.new_page()
                await page.set_viewport_size(
                    {"width": max(64, int(width)), "height": max(64, int(height))}
                )
                await page.set_content(
                    rendered_html,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await page.add_style_tag(
                    content="""
*, *::before, *::after {
    animation: none !important;
    transition: none !important;
    caret-color: transparent !important;
    scroll-behavior: auto !important;
}
"""
                )
                await self._wait_for_visual_stability(page)
                output_path = self._new_output_path()
                await page.screenshot(
                    path=str(output_path),
                    full_page=True,
                    type="png",
                )
                return str(output_path)
            except Exception:
                if not self.available:
                    await self._discard_browser()
                raise
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def close(self) -> None:
        await self._discard_browser()

    async def _render_template(
        self,
        template: str,
        data: dict[str, Any],
        template_key: str,
    ) -> str:
        compiled = self._template_cache.get(template_key)
        if compiled is None:
            async with self._template_lock:
                compiled = self._template_cache.get(template_key)
                if compiled is None:
                    try:
                        from jinja2 import Environment, select_autoescape

                        environment = Environment(
                            autoescape=select_autoescape(["html", "xml"]),
                            cache_size=32,
                        )
                        compiled = environment.from_string(template)
                    except Exception as error:
                        raise LocalRenderUnavailable(
                            f"template compilation failed: {error}"
                        ) from error
                    if len(self._template_cache) >= 12:
                        self._template_cache.pop(next(iter(self._template_cache)))
                    self._template_cache[template_key] = compiled

        try:
            return await asyncio.to_thread(compiled.render, **data)
        except Exception as error:
            raise LocalRenderUnavailable(
                f"template rendering failed: {error}"
            ) from error

    async def _wait_for_visual_stability(self, page: Any) -> None:
        try:
            await page.wait_for_function(
                "() => Array.from(document.images || []).every(image => image.complete)",
                timeout=int(self._IMAGE_WAIT_SECONDS * 1000),
            )
        except Exception:
            pass

        try:
            await asyncio.wait_for(
                page.evaluate(
                    """
async () => {
    if (document.fonts && document.fonts.ready) {
        await document.fonts.ready;
    }
}
"""
                ),
                timeout=self._FONT_WAIT_SECONDS,
            )
        except Exception:
            pass

    async def _discard_browser(self) -> None:
        context, browser, playwright = (
            self._context,
            self._browser,
            self._playwright,
        )
        self._context = None
        self._browser = None
        self._playwright = None
        for resource in (context, browser):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass
        stop = getattr(playwright, "stop", None)
        if callable(stop):
            try:
                await stop()
            except Exception:
                pass

    async def _launch_browser(self, playwright: Any) -> Any:
        executable = self._configured_browser_path()
        if executable:
            return await playwright.chromium.launch(
                executable_path=executable,
                headless=True,
            )

        for channel in ("chrome", "msedge"):
            try:
                return await playwright.chromium.launch(channel=channel, headless=True)
            except Exception:
                pass

        return await playwright.chromium.launch(headless=True)

    @staticmethod
    def _configured_browser_path() -> str | None:
        configured = os.environ.get("ASTRBOT_ZHENXUN_SIGN_BROWSER", "").strip()
        if configured and Path(configured).is_file():
            return configured

        candidates = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        ]
        if sys.platform == "win32":
            program_files = os.environ.get("ProgramFiles", "")
            program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            candidates.extend(
                [
                    str(Path(program_files) / "Google/Chrome/Application/chrome.exe")
                    if program_files
                    else "",
                    str(
                        Path(program_files_x86) / "Google/Chrome/Application/chrome.exe"
                    )
                    if program_files_x86
                    else "",
                    str(Path(local_app_data) / "Google/Chrome/Application/chrome.exe")
                    if local_app_data
                    else "",
                ]
            )
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        return None

    @staticmethod
    def _new_output_path() -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

            root = Path(get_astrbot_temp_path())
        except Exception:
            root = Path(tempfile.gettempdir()) / "astrbot"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"zhenxun_sign_{uuid.uuid4().hex}.png"
