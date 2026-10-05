#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.transport_security import TransportSecuritySettings

from fs_mcp import config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Розбирає аргументи командного рядка у конфігурацію сервера."""
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="MCP-сервер керування файловою системою поверх Streamable HTTP.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=config.DEFAULT_HOST, help="адреса прослуховування")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT, help="порт прослуховування")
    parser.add_argument("--path", default=config.MOUNT_PATH, help="шлях ендпоінта Streamable HTTP")
    parser.add_argument(
        "--root",
        default=None,
        help="каталог-пісочниця: усі файлові операції обмежуються ним (типово — вся ФС)",
    )
    parser.add_argument(
        "--allowed-hosts",
        default="",
        help="список допустимих Host через кому; вмикає DNS-rebinding protection "
        "(порожньо або '*' — protection вимкнено, наприклад за reverse proxy/тунелем)",
    )
    parser.add_argument("--max-read-bytes", type=int, default=config.DEFAULT_MAX_READ_BYTES, help="ліміт на розмір читаного/писаного файлу")
    parser.add_argument("--max-search-results", type=int, default=config.DEFAULT_MAX_SEARCH_RESULTS, help="стеля кількості результатів пошуку/перегляду")
    parser.add_argument("--exec-timeout", type=float, default=config.DEFAULT_EXEC_TIMEOUT, help="типовий таймаут виконання команди, сек")
    parser.add_argument("--exec-timeout-max", type=float, default=config.DEFAULT_EXEC_TIMEOUT_MAX, help="максимально допустимий таймаут команди, сек")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    allowed_hosts = [h.strip() for h in args.allowed_hosts.split(",") if h.strip()]

    config.configure(
        host=args.host,
        port=args.port,
        path=args.path,
        root=args.root,
        allowed_hosts=allowed_hosts,
        max_read_bytes=args.max_read_bytes,
        max_search_results=args.max_search_results,
        exec_timeout=args.exec_timeout,
        exec_timeout_max=args.exec_timeout_max,
    )

    from fs_mcp.app import server
    import fs_mcp.tools  # noqa: F401

    # Аудит викликів інструментів (ім'я, аргументи, тривалість, підсумок) у stderr -> server.log.
    from fs_mcp.audit import get_audit_logger, make_tool_audit_middleware
    from fs_mcp.auth import user_context_middleware

    server.middleware.append(user_context_middleware)
    server.middleware.append(make_tool_audit_middleware(get_audit_logger()))

    if config.ALLOWED_HOSTS and config.ALLOWED_HOSTS != ["*"]:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=config.ALLOWED_HOSTS,
            allowed_origins=[],
        )
    else:
        transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    print("[filesystem-mcp] starting Streamable HTTP server", file=sys.stderr)
    print(f"[filesystem-mcp] bind   = http://{config.HOST}:{config.PORT}{config.MOUNT_PATH}", file=sys.stderr)
    print(f"[filesystem-mcp] sandbox = {config.SANDBOX or 'whole filesystem'}", file=sys.stderr)
    print(f"[filesystem-mcp] dns-rebinding-protection = {transport_security.enable_dns_rebinding_protection}", file=sys.stderr)

    async def _run() -> None:
        await server.run_streamable_http_async(
            host=config.HOST,
            port=config.PORT,
            streamable_http_path=config.MOUNT_PATH,
            transport_security=transport_security,
            json_response=True,
        )

    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
