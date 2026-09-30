from __future__ import annotations

import shutil

from ..app import server
from ..config import resolve_path


@server.tool()
def rename_path(src: str, dst: str, overwrite: bool = False, create_dirs: bool = True) -> dict:
    """Перейменовує або переміщує файл чи каталог.

    Працює і для файлів, і для каталогів. Обидва шляхи `src` і `dst` розв'язуються
    та обмежуються пісочницею, якщо вона налаштована.

    Args:
        src: Наявний файл або каталог для перейменування/переміщення.
        dst: Новий шлях/назва (може бути в іншому каталозі).
        overwrite: Замінити `dst`, якщо він уже існує, коли True; інакше операцію
            відмовлено, щоб не перезаписати дані.
        create_dirs: Створити батьківський каталог `dst`, якщо він відсутній.
    """
    source = resolve_path(src)
    dest = resolve_path(dst)

    if not source.exists() and not source.is_symlink():
        raise FileNotFoundError(f"source does not exist: {source}")
    if source == dest:
        raise ValueError("src and dst resolve to the same path")
    if dest.exists() and not overwrite:
        raise FileExistsError(f"destination exists: {dest} (передайте overwrite=True для заміни)")
    # Не дозволяємо перемістити каталог усередину самого себе.
    if source.is_dir() and source in dest.parents:
        raise ValueError(f"не можна перемістити каталог усередину самого себе: {source} -> {dest}")

    if create_dirs:
        dest.parent.mkdir(parents=True, exist_ok=True)

    # Path.rename переміщує в межах ФС; shutil.move коректно працює і
    # між пристроями, і поверх наявного dst при overwrite.
    shutil.move(str(source), str(dest))
    return {"from": str(source), "to": str(dest), "renamed": True, "overwrite": overwrite}
