from __future__ import annotations

from ..app import server
from ..config import MAX_SEARCH_RESULTS, resolve_path


@server.tool()
def list_directory(path: str = ".", recursive: bool = False, include_hidden: bool = False) -> dict:
    """Перелічує записи каталогу.

    Args:
        path: Каталог для перегляду.
        recursive: Обходити все дерево, якщо True.
        include_hidden: Включати приховані файли (з крапкою), якщо True.
    """
    target = resolve_path(path)
    if not target.is_dir():
        raise NotADirectoryError(f"not a directory: {target}")

    entries = []
    it = target.rglob("*") if recursive else target.iterdir()
    for child in it:
        if not include_hidden and child.name.startswith("."):
            continue
        stat = child.stat()
        entries.append({
            "path": str(child),
            "type": "dir" if child.is_dir() else ("link" if child.is_symlink() else "file"),
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
        if len(entries) >= MAX_SEARCH_RESULTS:
            break
    return {"root": str(target), "count": len(entries), "entries": entries}
