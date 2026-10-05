from __future__ import annotations

import re

from ..app import server
from ..config import resolve_path
from ..syntax import check_syntax as validate_syntax, is_checkable


@server.tool()
def update_file(
    path: str,
    find: str,
    replace: str,
    occurrence: int | None = None,
    regex: bool = False,
    check_syntax: bool = True,
) -> dict:
    """Виконує цільову заміну (find-and-replace) всередині наявного файлу.

    Для файлів .py/.js/.php відредагований вміст перевіряється на синтаксис до
    запису; якщо перевірка не пройшла, файл лишається недоторканим (якщо
    `check_syntax` не False).

    Args:
        path: Файл для редагування.
        find: Текст (або regex, коли `regex`) для пошуку.
        replace: Текст заміни.
        occurrence: Індекс (відлік з 1), котрий збіг замінити; None замінює всі.
        regex: Вважати `find` регулярним виразом, коли True.
        check_syntax: Валідувати синтаксис для .py/.js/.php перед збереженням (типово True).
    """
    target = resolve_path(path, access="write")
    if not target.is_file():
        raise FileNotFoundError(f"not a file: {target}")

    original = target.read_text(encoding="utf-8", errors="replace")

    if regex:
        flags = 0
        if occurrence is None:
            new_text, count = re.subn(find, replace, original, flags=flags)
        else:
            new_text, count = re.subn(find, replace, original, count=occurrence, flags=flags)
            # subn(count=n) замінює перші n збігів; імітуємо заміну одного входження
            if occurrence > 0:
                matches = list(re.finditer(find, original, flags=flags))
                if len(matches) >= occurrence:
                    m = matches[occurrence - 1]
                    new_text = original[:m.start()] + replace + original[m.end():]
                    count = 1
    else:
        if occurrence is None:
            count = original.count(find)
            new_text = original.replace(find, replace)
        else:
            idx = -1
            for _ in range(occurrence):
                idx = original.find(find, idx + 1)
                if idx == -1:
                    break
            if idx == -1:
                count = 0
                new_text = original
            else:
                new_text = original[:idx] + replace + original[idx + len(find):]
                count = 1

    if count == 0:
        return {"path": str(target), "replacements": 0, "changed": False, "message": "no match found"}

    syntax_checked = False
    if check_syntax and is_checkable(target):
        ok, err = validate_syntax(target, new_text)
        syntax_checked = True
        if not ok:
            return {
                "path": str(target),
                "replacements": count,
                "changed": False,
                "syntax_ok": False,
                "error": err,
                "message": "файл не змінено: правка ламає синтаксис (передайте check_syntax=False для примусового збереження)",
            }

    target.write_text(new_text, encoding="utf-8")
    result = {"path": str(target), "replacements": count, "changed": True}
    if syntax_checked:
        result["syntax_ok"] = True
    return result
