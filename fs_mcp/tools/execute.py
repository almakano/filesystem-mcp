from __future__ import annotations

import os
import shlex
import signal
import subprocess

from ..app import server
from ..config import (
    EXEC_TIMEOUT_DEFAULT,
    EXEC_TIMEOUT_MAX,
    MAX_READ_BYTES,
    resolve_path,
)


@server.tool()
def execute_command(command: str, cwd: str = ".", timeout: float = EXEC_TIMEOUT_DEFAULT, shell: bool = False) -> dict:
    """Виконує команду оболонки та захоплює її вивід.

    Args:
        command: Командний рядок для запуску. Коли `shell` False — розбирається через
            shlex; коли True — передається системній оболонці.
        cwd: Робочий каталог для процесу.
        timeout: Максимум секунд реального часу, після яких процес буде завершено примусово.
        shell: Виконати через /bin/sh -c (або cmd /c) замість exec-форми.
    """
    workdir = resolve_path(cwd) if cwd else None
    timeout = min(max(0.1, timeout), EXEC_TIMEOUT_MAX)

    if shell:
        argv: str | list[str] = command
    else:
        argv = shlex.split(command)
        if not argv:
            raise ValueError("empty command")

    popen_kwargs: dict = dict(
        cwd=str(workdir) if workdir else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if shell:
        popen_kwargs["shell"] = True
    # Від'єднуємо у нову групу процесів, щоб примусово завершити все дерево за таймаутом.
    popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603 - навмисний exec-інструмент
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
        out, err = proc.communicate()
        return {"command": command, "timed_out": True, "returncode": -9, "stdout": out, "stderr": err}

    return {
        "command": command,
        "returncode": proc.returncode,
        "stdout": out[-MAX_READ_BYTES:] if out else "",
        "stderr": err[-MAX_READ_BYTES:] if err else "",
        "timed_out": False,
    }
