from __future__ import annotations

from pathlib import Path

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8222
DEFAULT_MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MiB
DEFAULT_MAX_SEARCH_RESULTS = 100
DEFAULT_EXEC_TIMEOUT = 60.0
DEFAULT_EXEC_TIMEOUT_MAX = 7200.0

HOST: str = DEFAULT_HOST
PORT: int = DEFAULT_PORT
MOUNT_PATH: str = "/llm/mcp/{user}"
ALLOWED_HOSTS: list[str] = []

SANDBOX: Path | None = None

MAX_READ_BYTES: int = DEFAULT_MAX_READ_BYTES
MAX_SEARCH_RESULTS: int = DEFAULT_MAX_SEARCH_RESULTS
EXEC_TIMEOUT_DEFAULT: float = DEFAULT_EXEC_TIMEOUT
EXEC_TIMEOUT_MAX: float = DEFAULT_EXEC_TIMEOUT_MAX


def configure(
    *,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    root: str | None = None,
    allowed_hosts: list[str] | None = None,
    max_read_bytes: int | None = None,
    max_search_results: int | None = None,
    exec_timeout: float | None = None,
    exec_timeout_max: float | None = None,
) -> None:
    """Застосовує конфігурацію з розібраних аргументів командного рядка.

    Будь-яке значення ``None`` залишає поточний (дефолтний) параметр без змін.
    Викликається один раз на старті сервера до імпорту модулів інструментів.
    """
    global HOST, PORT, MOUNT_PATH, ALLOWED_HOSTS, SANDBOX
    global MAX_READ_BYTES, MAX_SEARCH_RESULTS, EXEC_TIMEOUT_DEFAULT, EXEC_TIMEOUT_MAX

    if host is not None:
        HOST = host
    if port is not None:
        PORT = port
    if path is not None:
        MOUNT_PATH = path
    if root is not None:
        SANDBOX = Path(root).expanduser().resolve()
    if allowed_hosts is not None:
        ALLOWED_HOSTS = allowed_hosts
    if max_read_bytes is not None:
        MAX_READ_BYTES = max_read_bytes
    if max_search_results is not None:
        MAX_SEARCH_RESULTS = max_search_results
    if exec_timeout is not None:
        EXEC_TIMEOUT_DEFAULT = exec_timeout
    if exec_timeout_max is not None:
        EXEC_TIMEOUT_MAX = exec_timeout_max


def resolve_path(path: str, *, access: str = "read") -> Path:
    """Розв'язує шлях і перевіряє sandbox та права вибраного MCP-користувача."""
    if not path:
        raise ValueError("path must not be empty")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        base = SANDBOX or Path.cwd()
        candidate = base / candidate
    resolved = candidate.resolve()
    if SANDBOX is not None:
        try:
            resolved.relative_to(SANDBOX)
        except ValueError:
            raise PermissionError(f"path escapes sandbox root {SANDBOX}: {path}")
    from .auth import require_access

    if access == "read":
        require_access(resolved, read=True)
    elif access == "write":
        require_access(resolved, write=True)
    elif access == "traverse":
        require_access(resolved, execute=True)
    elif access != "none":
        raise ValueError(f"unknown access mode: {access}")
    return resolved
