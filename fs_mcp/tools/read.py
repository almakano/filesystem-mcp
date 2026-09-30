from __future__ import annotations

from ..app import server
from ..config import MAX_READ_BYTES, resolve_path


@server.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict:
    """Читає текстовий файл, за бажанням — конкретний діапазон рядків (відлік з 1).

    Args:
        path: Файл для читання.
        start_line: Перший рядок для повернення (відлік з 1, включно).
        end_line: Останній рядок для повернення (відлік з 1, включно); None означає до кінця файлу.
    """
    target = resolve_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"not a file: {target}")

    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(f"file too large ({size} bytes); max {MAX_READ_BYTES}")

    text = target.read_text(errors="replace")
    lines = text.splitlines()
    lo = max(1, start_line)
    hi = end_line if end_line is not None else len(lines)
    hi = min(hi, len(lines))
    selected = "\n".join(lines[lo - 1:hi]) if hi >= lo else ""
    return {
        "path": str(target),
        "total_lines": len(lines),
        "returned_range": [lo, hi],
        "content": selected,
    }
