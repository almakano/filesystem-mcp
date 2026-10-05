from __future__ import annotations

import os
import shutil

from ..app import server
from ..auth import require_access
from ..config import resolve_path


@server.tool()
def delete_path(path: str, recursive: bool = False) -> dict:
    """Видаляє файл, символічне посилання або каталог.

    Args:
        path: Файл чи каталог для видалення.
        recursive: Видаляти непорожнений каталог з деревом, якщо True. Каталог
            видаляється, лише якщо він порожній (або `recursive` True); це захищає
            від випадкової втрати даних.
    """
    target = resolve_path(path, access="none")
    require_access(target.parent, write=True)
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(f"path does not exist: {target}")

    if target.is_dir() and not target.is_symlink():
        if recursive:
            shutil.rmtree(target)
            return {"path": str(target), "type": "dir", "deleted": True, "recursive": True}
        try:
            os.rmdir(target)
        except OSError as exc:
            raise OSError(
                f"каталог не порожній: {target} (передайте recursive=True для рекурсивного видалення): {exc}"
            ) from exc
        return {"path": str(target), "type": "dir", "deleted": True, "recursive": False}

    os.unlink(target)
    return {"path": str(target), "type": "file", "deleted": True, "recursive": False}
