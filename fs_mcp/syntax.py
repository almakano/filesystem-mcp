from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
from pathlib import Path

CHECKABLE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".php"}
_TIMEOUT = 15


def is_checkable(path: str | Path) -> bool:
    """True, якщо для розширення файлу є перевірка синтаксису."""
    return Path(path).suffix.lower() in CHECKABLE_SUFFIXES


def check_syntax(path: str | Path, content: str) -> tuple[bool, str | None]:
    """Перевіряє синтаксис `content` за розширенням `path`.

    Повертає (ok, message). ok=True означає «записувати можна»: або
    синтаксис коректний, або розширення не перевіряється / лінтер недоступний.
    ok=False супроводжується текстом помилки.
    """
    ext = Path(path).suffix.lower()

    if ext == ".py":
        return _check_python(content)
    if ext in (".js", ".mjs", ".cjs"):
        return _check_with_binary("node", ["--check", "{file}"], ext, content)
    if ext == ".php":
        return _check_with_binary("php", ["-l", "{file}"], ext, content)
    return True, None


def _check_python(content: str) -> tuple[bool, str | None]:
    try:
        ast.parse(content)
        return True, None
    except SyntaxError as exc:
        loc = ""
        if exc.lineno is not None:
            loc = f":{exc.lineno}"
            if exc.offset is not None:
                loc += f":{exc.offset}"
        return False, f"Python syntax error{loc}: {exc.msg}"


def _check_with_binary(binary: str, args: list[str], suffix: str, content: str) -> tuple[bool, str | None]:
    if shutil.which(binary) is None:
        return True, None

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as fh:
            fh.write(content)
            tmp_path = Path(fh.name)

        cmd = [binary] + [a.replace("{file}", str(tmp_path)) for a in args]
        proc = subprocess.run(  # noqa: S603 - фіксований бінарник та аргументи
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=_TIMEOUT,
        )
    except FileNotFoundError:
        return True, None
    except subprocess.TimeoutExpired:
        return False, f"{binary} syntax check timed out after {_TIMEOUT}s"
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    if proc.returncode == 0:
        return True, None

    output = (proc.stdout or "").strip()
    if tmp_path is not None:
        output = output.replace(str(tmp_path), "<content>")
    label = {"node": "JS", "php": "PHP"}.get(binary, binary)
    return False, f"{label} syntax error: {output}"
