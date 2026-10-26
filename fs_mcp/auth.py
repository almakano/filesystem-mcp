from __future__ import annotations

import contextvars
import os
import pwd
import re
from pathlib import Path
from typing import Any

from mcp.server.lowlevel.server import ServerRequestContext

_USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.IGNORECASE)
_current_user: contextvars.ContextVar[pwd.struct_passwd | None] = contextvars.ContextVar("mcp_current_user", default=None)


def _lookup_user(name: str) -> pwd.struct_passwd:
    if not _USER_RE.fullmatch(name):
        raise PermissionError("invalid MCP user")
    try:
        account = pwd.getpwnam(name)
    except KeyError as exc:
        raise PermissionError(f"unknown MCP user: {name}") from exc
    if account.pw_uid != 0 and account.pw_uid < 1000:
        raise PermissionError("MCP user must be a regular system user or root")
    if not account.pw_shell or account.pw_shell.endswith("/nologin"):
        raise PermissionError("MCP user is not enabled for MCP access")
    return account


def get_user() -> pwd.struct_passwd:
    user = _current_user.get()
    if user is None:
        raise PermissionError("MCP user context is not established")
    return user


def set_user(name: str) -> contextvars.Token:
    return _current_user.set(_lookup_user(name))


def reset_user(token: contextvars.Token) -> None:
    _current_user.reset(token)


def user_home() -> Path:
    """Домашній («робочий») каталог авторизованого MCP-користувача."""
    return Path(get_user().pw_dir)


def user_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Окруження для запуску команд від імені авторизованого MCP-користувача.

    Бере за основу поточне окруження процесу сервера й підміняє змінні, що мають
    вказувати на цільового користувача, а не на сервер (зазвичай root). Викликається
    у батьківському процесі до fork, тому get_user() тут доступний.
    """
    user = get_user()
    env = dict(base if base is not None else os.environ)
    env["HOME"] = user.pw_dir
    env["USER"] = user.pw_name
    env["LOGNAME"] = user.pw_name
    env["SHELL"] = user.pw_shell or "/bin/sh"
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    return env


def _header_user(ctx: ServerRequestContext[Any, Any]) -> str | None:
    request = ctx.request
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is not None:
        value = headers.get("x-mcp-user")
        if value:
            return value.strip()
    path = getattr(request, "url", None)
    path = getattr(path, "path", "") if path is not None else ""
    match = re.fullmatch(r"/llm/mcp/([^/]+)/?", path)
    return match.group(1) if match else None


async def user_context_middleware(ctx: ServerRequestContext[Any, Any], call_next):
    name = _header_user(ctx)
    if not name:
        raise PermissionError("MCP URL must be /llm/mcp/<user>")
    token = set_user(name)
    try:
        return await call_next(ctx)
    finally:
        reset_user(token)


def _groups(user: pwd.struct_passwd) -> set[int]:
    return set(os.getgrouplist(user.pw_name, user.pw_gid))


def _permitted_bits(st: os.stat_result, user: pwd.struct_passwd) -> int:
    """Біти прав, які реально діють для користувача (owner/group/other)."""
    if st.st_uid == user.pw_uid:
        return (st.st_mode >> 6) & 0o7
    if st.st_gid in _groups(user):
        return (st.st_mode >> 3) & 0o7
    return st.st_mode & 0o7


def _allowed(st: os.stat_result, user: pwd.struct_passwd, required: int) -> bool:
    """Чи дозволена дія з бітами ``required`` для об'єкта зі станом ``st``.

    Для root враховуємо CAP_DAC_OVERRIDE: читання й запис не обмежуються бітами,
    а виконання/обхід каталогу потребує хоч одного виставленого біта ``x``.
    """
    if user.pw_uid == 0:
        if not (required & 0o1):
            return True
        return bool(st.st_mode & 0o111)
    return (_permitted_bits(st, user) & required) == required


def _has_mode(path, required: int, user: pwd.struct_passwd) -> bool:
    return _allowed(path.stat(), user, required)


def require_access(path, *, read: bool = False, write: bool = False, execute: bool = False) -> None:
    user = get_user()
    target = path.resolve(strict=False)

    # Обхід (біт x) перевіряємо лише для каталогів: сам цільовий файл не мусить
    # мати біт виконання, щоб його можна було прочитати чи записати.
    anchor = target if target.is_dir() else target.parent
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent

    for parent in [anchor, *anchor.parents]:
        if parent == parent.parent:
            break
        if not _allowed(parent.stat(), user, 0o1):
            raise PermissionError(f"user {user.pw_name} cannot traverse {parent}")

    if not target.exists():
        if write and not _allowed(anchor.stat(), user, 0o3):
            raise PermissionError(f"user {user.pw_name} cannot write {anchor}")
        return

    if read:
        required = 0o5 if target.is_dir() else 0o4
        if not _has_mode(target, required, user):
            raise PermissionError(f"user {user.pw_name} cannot read {target}")
    if write and not _has_mode(target, 0o2, user):
        raise PermissionError(f"user {user.pw_name} cannot write {target}")
    if execute and not _has_mode(target, 0o1, user):
        raise PermissionError(f"user {user.pw_name} cannot execute {target}")


def preexec_as_user() -> None:
    user = get_user()
    if os.geteuid() != 0:
        if os.geteuid() != user.pw_uid:
            raise PermissionError("MCP process is not privileged to switch user")
        return
    os.initgroups(user.pw_name, user.pw_gid)
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
