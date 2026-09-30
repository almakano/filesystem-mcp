from __future__ import annotations

import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.context import HandlerResult, ServerRequestContext

_LOGGER_NAME = "fs_mcp.audit"
_MAX_STR = 120  # поріг скорочення довгих рядкових аргументів (content тощо)


def get_audit_logger() -> logging.Logger:
    """Повертає логер аудиту, одного разу налаштува вивід у stderr."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [audit] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _compact(value: Any) -> Any:
    """Стискає довге значення, щоб запис журналу залишися коротким і у один рядок."""
    if isinstance(value, str):
        if len(value) > _MAX_STR:
            return f"<str {len(value)}b> {value[:_MAX_STR]!r}…"
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)}b>"
    if isinstance(value, (dict, list, tuple)):
        text = repr(value)
        if len(text) > _MAX_STR:
            return f"<{type(value).__name__} {len(text)}b>"
        return value
    return value


def _summarize(arguments: Any) -> Any:
    """Готує аргументи інструмента для журналу (скорочує великі поля)."""
    if isinstance(arguments, dict):
        return {key: _compact(val) for key, val in arguments.items()}
    return _compact(arguments)


def _is_error(result: Any) -> bool:
    """Визначає прапорець помилки результату: працює і з моделлю, і з dict (alias ``isError``)."""
    if result is None:
        return False
    if isinstance(result, dict):
        return bool(result.get("isError") or result.get("is_error"))
    value = getattr(result, "is_error", None)
    if value is None:
        value = getattr(result, "isError", None)
    return bool(value)


def _error_text(result: Any) -> Any:
    """Дістає текст першого content-блоку результату (для запису про невдачу)."""
    content = result.get("content") if isinstance(result, dict) else getattr(result, "content", None)
    if not content:
        return None
    first = content[0]
    text = first.get("text") if isinstance(first, dict) else getattr(first, "text", None)
    return _compact(text) if text is not None else None


def make_tool_audit_middleware(logger: logging.Logger) -> Callable[..., Awaitable[HandlerResult]]:
    """Створює middleware, що логує ім'я, аргументи, тривалість та підсумок виклику."""

    async def audit_middleware(
        ctx: ServerRequestContext[Any, Any],
        call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[HandlerResult]],
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)

        params = ctx.params or {}
        name = params.get("name")
        args = _summarize(params.get("arguments") or {})
        start = time.perf_counter()
        logger.info("tool_call start name=%s args=%r", name, args)

        try:
            result = await call_next(ctx)
        except Exception as exc:  # noqa: BLE001 — фіксуємо та передаємо далі
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error("tool_call error name=%s duration_ms=%.1f exc=%r", name, duration_ms, str(exc))
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        if _is_error(result):
            logger.warning(
                "tool_call done name=%s duration_ms=%.1f is_error=True error=%r",
                name,
                duration_ms,
                _error_text(result),
            )
        else:
            logger.info("tool_call done name=%s duration_ms=%.1f is_error=False", name, duration_ms)
        return result

    return audit_middleware
