from __future__ import annotations

import subprocess

from ..app import server
from ..auth import preexec_as_user, user_env
from ..config import MAX_READ_BYTES, MAX_SEARCH_RESULTS, resolve_path


def _run_rg(args: list[str], cwd: str) -> str:
    """Запускає ripgrep від імені авторизованого MCP-користувача та повертає stdout.

    Виконується у `cwd` (уже резв'язаному й перевіреному щодо sandbox/прав), тож
    подальші обмеження доступу до файлів застосовує сама ОС під цільовим користувачем.
    """
    try:
        proc = subprocess.run(
            ["rg", *args],
            cwd=cwd,
            env=user_env(),
            preexec_fn=preexec_as_user,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ripgrep (rg) is not available on this server") from exc
    # Коди виходу rg: 0 — є збіги, 1 — немає збігів, >=2 — помилка.
    if proc.returncode >= 2:
        raise RuntimeError(f"rg failed: {proc.stderr.strip()}")
    return proc.stdout


@server.tool()
def search_files(
    pattern: str,
    root: str = ".",
    search_contents: bool = False,
    glob: str = "*",
    case_sensitive: bool = False,
    max_results: int = MAX_SEARCH_RESULTS,
    cwd: str = ".",
) -> dict:
    """Шукає у файловій системі за іменем файлу, за бажанням — за вмістом (через ripgrep).

    Args:
        pattern: Підрядок для пошуку в імені та у вмісті (обробляється як літеральний
            рядок, не як regex).
        root: Каталог, всередині якого шукати.
        search_contents: Якщо True, також шукати `pattern` усередині текстових файлів.
        glob: Брати до уваги лише файли, чиє ім'я відповідає цьому glob (напр. "*.py").
        case_sensitive: Враховувати регістр, якщо True.
        max_results: Обмеження на кількість повернутих збігів.
        cwd: Робочий каталог; відносний `root` розв'язується від нього (типово —
            домашній каталог MCP-користувача).
    """
    base = resolve_path(root, cwd=cwd)
    if not base.exists():
        raise FileNotFoundError(f"path does not exist: {base}")

    base_str = str(base)
    # Відтворуємо попередню поведінку base.rglob: враховуємо приховані файли й
    # ігноруємо .gitignore; колір вимкнено для машинного розбору.
    common = ["--hidden", "--no-ignore", "--color", "never"]
    if not case_sensitive:
        common.append("--ignore-case")
    if glob and glob != "*":
        common.extend(["--glob", glob])

    needle = pattern if case_sensitive else pattern.lower()
    name_matches: list[str] = []
    content_matches: list[dict] = []

    # Перелік файлів бере `rg --files` (замість walk у Python).
    for rel in _run_rg(["--files", *common], cwd=base_str).splitlines():
        if not rel:
            continue
        name = rel.rsplit("/", 1)[-1]
        hay = name if case_sensitive else name.lower()
        if needle in hay:
            name_matches.append(str(base / rel))
            if len(name_matches) >= max_results:
                break

    if search_contents:
        # --null → NUL після імені файлу, щоб шлях з двокрапками не ламав розбір;
        # --max-filesize обмежує розмір (аналог перевірки MAX_READ_BYTES).
        args = ["--no-heading", "--line-number", "--null", "--fixed-strings",
                f"--max-filesize={MAX_READ_BYTES}", *common, "--", pattern]
        for line in _run_rg(args, cwd=base_str).splitlines():
            path_part, sep, rest = line.partition("\0")
            if not sep:
                continue
            lineno_part, colon, text = rest.partition(":")
            if not colon:
                continue
            content_matches.append({
                "path": str(base / path_part),
                "line": int(lineno_part),
                "text": text.strip()[:300],
            })
            if len(content_matches) >= max_results:
                break

    return {
        "root": base_str,
        "pattern": pattern,
        "name_matches": name_matches[:max_results],
        "content_matches": content_matches[:max_results],
    }
