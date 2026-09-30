from __future__ import annotations

import os
from pathlib import Path

from ..app import server
from ..config import resolve_path
from ..syntax import check_syntax as validate_syntax, is_checkable


def _existing_ancestor_owner(target: Path) -> tuple[int, int] | None:
    """Повертає (uid, gid) найближчого існуючого каталогу-предка шляху.

    Зазвичай це безпосередній parent, але якщо його ще немає — піднімаємось вище,
    до першого реально існуючого каталогу. Повертаємо None лише в недосяжному
    випадку (відсутній навіть корінь ФС).
    """
    parent = target.parent
    while True:
        try:
            st = parent.stat()
        except OSError:
            grandparent = parent.parent
            if grandparent == parent:  # дійшли до кореня
                return None
            parent = grandparent
            continue
        return st.st_uid, st.st_gid


def _missing_parent_dirs(target: Path) -> list[Path]:
    """Список ще не існуючих каталогів за шляхом до target (будуть створені)."""
    missing: list[Path] = []
    p = target.parent
    while p != p.parent and not p.exists():
        missing.append(p)
        p = p.parent
    return missing


def _chown_tree(paths: list[Path], uid: int, gid: int) -> bool:
    """Проставляє власника/групу об'єктам best-effort.

    Без прав root ``chown`` може не вдаватися — тоді просто пропускаємо об'єкт,
    не скасовуючи запис файлу.
    """
    applied_any = False
    for p in paths:
        try:
            os.chown(p, uid, gid)
            applied_any = True
        except OSError:
            continue
    return applied_any


@server.tool()
def write_file(
    path: str,
    content: str,
    append: bool = False,
    create_dirs: bool = True,
    check_syntax: bool = True,
) -> dict:
    """Створює або перезаписує (або дозаписує) текстовий файл.

    Для файлів .py/.js/.php підсумковий вміст перевіряється на синтаксис до
    запису; якщо перевірка не пройшла, файл лишається недоторканим (якщо
    `check_syntax` не False).

    Щойно створені файли та каталоги успадковують власника/групу найближчого
    існуючого батьківського каталогу (best-effort; пропускається, коли не дозволено).

    Args:
        path: Файл призначення.
        content: Текст для запису.
        append: Дозаписувати замість обрізання, коли True.
        create_dirs: Створювати батьківські каталоги, якщо вони відсутні.
        check_syntax: Валідувати синтаксис для .py/.js/.php перед записом (типово True).
    """
    target = resolve_path(path)

    # Формуємо підсумковий вміст (з урахуванням дозапису) і перевіряємо синтаксис.
    resulting = content
    if append and target.is_file():
        resulting = target.read_text(encoding="utf-8", errors="replace") + content

    syntax_checked = False
    if check_syntax and is_checkable(target):
        ok, err = validate_syntax(target, resulting)
        syntax_checked = True
        if not ok:
            return {
                "path": str(target),
                "written": False,
                "syntax_ok": False,
                "error": err,
                "message": "файл не змінено: помилка синтаксису (передайте check_syntax=False для примусового запису)",
            }

    # Власник і група нових об'єктів успадковуються від найближчого існуючого
    # каталогу-предка (як за звичайного створення файлів у shell).
    inherited_owner = _existing_ancestor_owner(target)

    created_dirs: list[Path] = []
    if create_dirs:
        created_dirs = _missing_parent_dirs(target)
        target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(target, mode, encoding="utf-8") as fh:
        fh.write(content)

    ownership_paths = [*created_dirs, target]
    ownership_applied = False
    if inherited_owner is not None:
        uid, gid = inherited_owner
        ownership_applied = _chown_tree(ownership_paths, uid, gid)

    result = {
        "path": str(target),
        "written": True,
        "bytes_written": len(content.encode("utf-8")),
        "mode": "append" if append else "overwrite",
    }
    if syntax_checked:
        result["syntax_ok"] = True
    if ownership_applied:
        result["ownership"] = {
            "uid": uid,
            "gid": gid,
            "applied_to": [str(p) for p in ownership_paths],
        }
    return result
