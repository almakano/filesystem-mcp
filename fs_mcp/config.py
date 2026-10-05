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


def _expand(text: str, base: Path, home: Path) -> Path:
    """Розгортає ``~``/``~/`` від home та відносні шляхи від ``base`` (без resolve)."""
    t = str(text)
    # ~ та ~/ завжди розгортаємо від домашнього каталогу MCP-користувача,
    # а не від HOME процесу сервера.
    if t == "~":
        return home
    if t.startswith("~/"):
        return home / t[2:]
    candidate = Path(t)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate


def resolve_path(path: str, *, access: str = "read", cwd: str | None = None) -> Path:
    """Розв'язує шлях і перевіряє sandbox та права вибраного MCP-користувача.

    Відносні шляхи та префікс ``~``/``~/`` рахуються від домашнього («робочого»)
    каталогу авторизованого MCP-користувача; якщо налаштовано пісочницю, базою
    для відносних шляхів слугує вона.

    Якщо передано ``cwd``, він стає базою для відносних шляхів замість домашнього
    каталогу/пісочниці; сам ``cwd`` резв'язується так само (відносний ``cwd`` — від
    домашнього каталогу/пісочниці, ``~`` — від домашнього каталогу). Абсолютний
    ``path`` та префікс ``~`` у ``path`` ігнорують ``cwd``.
    """
    if not path:
        raise ValueError("path must not be empty")
    from .auth import require_access, user_home

    home = user_home()
    default_base = SANDBOX or home
    # База для відносних шляхів: налаштований cwd (резолвимо його від домашнього
    # каталогу/пісочниці) або, як раніше, SANDBOX/home.
    base = default_base if not cwd else _expand(cwd, default_base, home).resolve()
    candidate = _expand(path, base, home).resolve()
    if SANDBOX is not None:
        try:
            candidate.relative_to(SANDBOX)
        except ValueError:
            raise PermissionError(f"path escapes sandbox root {SANDBOX}: {path}")

    if access == "read":
        require_access(candidate, read=True)
    elif access == "write":
        require_access(candidate, write=True)
    elif access == "traverse":
        require_access(candidate, execute=True)
    elif access != "none":
        raise ValueError(f"unknown access mode: {access}")
    return candidate
