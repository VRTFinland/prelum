import asyncio
from functools import cache

from app.core.config import get_settings
from app.render.renderer import TypstRenderer

__all__ = ["get_render_semaphore", "get_renderer", "get_settings"]


@cache
def get_renderer() -> TypstRenderer:
    return TypstRenderer(get_settings())


@cache
def get_render_semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().max_concurrent_renders)
