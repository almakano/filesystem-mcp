from __future__ import annotations

import fnmatch

from ..app import server
from ..config import MAX_READ_BYTES, MAX_SEARCH_RESULTS, resolve_path


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
    """Шукає у файловій системі за іменем файлу, за бажанням — за вмістом.

    Args:
        pattern: Підрядок (режим імені) або regex/підрядок (режим вмісту) для пошуку.
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

    needle = pattern if case_sensitive else pattern.lower()
    name_matches: list[str] = []
    content_matches: list[dict] = []
    scanned = 0

    for child in base.rglob("*"):
        if not child.is_file():
            continue
        if not fnmatch.fnmatch(child.name, glob):
            continue
        hay = child.name if case_sensitive else child.name.lower()
        if needle in hay:
            name_matches.append(str(child))
        if search_contents:
            scanned += 1
            try:
                if child.stat().st_size > MAX_READ_BYTES:
                    continue
                text = child.read_text(errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                cmp_line = line if case_sensitive else line.lower()
                if needle in cmp_line:
                    content_matches.append({"path": str(child), "line": lineno, "text": line.strip()[:300]})
                    if len(content_matches) >= max_results:
                        break
            if len(content_matches) >= max_results:
                break
        if len(name_matches) >= max_results:
            break

    return {
        "root": str(base),
        "pattern": pattern,
        "name_matches": name_matches[:max_results],
        "content_matches": content_matches[:max_results],
    }
