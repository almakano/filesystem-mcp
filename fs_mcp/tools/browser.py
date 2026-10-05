from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from ..app import server
from ..auth import preexec_as_user
from ..config import EXEC_TIMEOUT_MAX, MAX_READ_BYTES, resolve_path

# Типовий реальний ліміт усього запуску браузера (сек).
DEFAULT_BROWSER_TIMEOUT = 60.0
# Маркер, яким гарнес відділяє итоговий JSON-результат від решти stdout.
_RESULT_MARKER = "__MCP_BROWSER_RESULT__"

# Кандидати, у чьому `node_modules` може лежати puppeteer (у порядку приоритета).
# Першим — явный перевизначувач через змінну середовища.
_PUPPETEER_CANDIDATES = (
    os.environ.get("MCP_PUPPETEER_ROOT"),
    "/opt/agy-wrapper",
    "/opt/mcpserver",
)

# Гарнес: запускає тіло сценарію як асинхронну функцію, якій доступні
# `page`, `browser` і `puppeteer`, і печатает результат після маркера.
_HARNESS = r"""
'use strict';
const fs = require('fs');

const MARKER = process.env.__MCP_MARKER || '__MCP_BROWSER_RESULT__';

function emit(payload) {
  // Окремий рядок: \n + MARKER + <json> + \n, щоб надійно витягнути за маркером.
  process.stdout.write('\n' + MARKER + JSON.stringify(payload) + '\n');
}

function serialize(value) {
  if (value === undefined) return null;
  try {
    // Глибока копія через JSON: відсікає функції/circular і нормалізує Date тощо.
    return JSON.parse(JSON.stringify(value));
  } catch (e) {
    try { return String(value); } catch (_) { return '[unserializable]'; }
  }
}

(async () => {
  let browser;
  try {
    const puppeteer = require('puppeteer');
    const scriptPath = process.env.__MCP_SCRIPT;
    const url = process.env.__MCP_URL || '';
    const navTimeout = parseInt(process.env.__MCP_NAV_TIMEOUT || '30000', 10);
    const userScript = fs.readFileSync(scriptPath, 'utf8');

    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    });
    const page = await browser.newPage();
    page.setDefaultTimeout(navTimeout);
    if (url) {
      await page.goto(url, { waitUntil: 'networkidle2', timeout: navTimeout });
    }

    // Тіло сценарію виконується як async-функція: доступні page/browser/puppeteer,
    // результат повертається через `return`.
    const scenario = new Function(
      'page', 'browser', 'puppeteer',
      '"use strict"; return (async () => { ' + userScript + ' })();'
    );
    const result = await scenario(page, browser, puppeteer);
    emit({ ok: true, result: serialize(result) });
  } catch (err) {
    emit({ ok: false, error: (err && err.stack) ? String(err.stack) : String(err) });
  } finally {
    if (browser) { try { await browser.close(); } catch (e) {} }
  }
})();
"""


def _resolve_puppeteer_root() -> Path | None:
    """Знаходить каталог, у чиєму `node_modules` лежить пакет `puppeteer`."""
    for raw in _PUPPETEER_CANDIDATES:
        if not raw:
            continue
        base = Path(raw).expanduser()
        if (base / "node_modules" / "puppeteer").is_dir():
            return base
    cwd = Path.cwd()
    if (cwd / "node_modules" / "puppeteer").is_dir():
        return cwd
    return None


def _split_result(stdout: str) -> tuple[dict | None, str]:
    """Розділює stdout гарнеса: (payload, чистий вивід сценарію).

    return: payload — розпарсений JSON, надрукований після маркера (або None, якщо
    маркера немає); output — усе, що було надруковано ДО маркера (console.log тощо),
    без самої службової рядки результату.
    """
    idx = stdout.rfind(_RESULT_MARKER)
    if idx == -1:
        return None, stdout
    payload: dict | None = None
    tail = stdout[idx + len(_RESULT_MARKER):]
    line = tail.splitlines()[0] if tail.strip() else ""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        payload = None
    output = stdout[:idx].rstrip("\n")
    return payload, output


@server.tool()
def test_in_browser(script: str, url: str = "", timeout: float = DEFAULT_BROWSER_TIMEOUT, cwd: str = ".") -> dict:
    """Виконує Puppeteer-сценарій у headless Chrome (через Node.js) і повертає результат.

    `script` — тіло асинхронної функції, якій доступні `page` (об'єкт Puppeteer
    Page), `browser` і `puppeteer`. Сценарій може робити `await page.goto(...)` /
    `page.click(...)` / `page.evaluate(...)` тощо і повертає значення через `return`
    (воно серіалізується в JSON). Якщо `url` задано, гарнес сам перейти на нього
    (waitUntil='networkidle2') перед запуском тіла; інакше навігація на совісті
    сценарію.

    Args:
        script: Тіло JS-сценарію з доступним `page`.
        url: Необов'язкова стартова адреса для `page.goto`.
        timeout: Загальний ліміт секунд на запуск; вкладається в межу exec-таймаута.
        cwd: Робочий каталог процесу; відносні шляхи у сценарії (напр. для
            скриншотів/файлів) розв'язуються від нього (типово — домашній каталог
            MCP-користувача).

    Returns:
        dict з `ok` (bool); при успіху — `result` (повернуте сценарієм значення) і
        `output` (stdout сценарію, напр. console.log); при невдачі — `error`. Також
        додаються `timed_out`/`returncode` для діагностики.
    """
    if not script or not script.strip():
        raise ValueError("script must not be empty")

    # cwd ""/None → «.» → домашній каталог користувача (див. config.resolve_path).
    workdir = resolve_path(cwd or ".", access="traverse")

    node = shutil.which("node")
    if node is None:
        return {"ok": False, "error": "node not found in PATH"}

    root = _resolve_puppeteer_root()
    if root is None:
        return {
            "ok": False,
            "error": "puppeteer не знайдено у `node_modules` жодного з каталогів-кандидатів; "
                     "задайте MCP_PUPPETEER_ROOT або встановіть `npm i puppeteer`",
            "searched": [c for c in _PUPPETEER_CANDIDATES if c],
        }

    # Загальний ліміт; грант +10s на старт/закриття Chrome, щоб JS-таймаут спрацював першим.
    timeout = min(max(1.0, timeout), EXEC_TIMEOUT_MAX)
    nav_ms = int(timeout * 1000)

    tmp_dir = Path(tempfile.mkdtemp(prefix="mcp_browser_"))
    harness_path = tmp_dir / "harness.cjs"
    script_path = tmp_dir / "scenario.js"
    try:
        harness_path.write_text(_HARNESS, encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")

        env = dict(os.environ)
        env["NODE_PATH"] = str(root / "node_modules") + os.pathsep + env.get("NODE_PATH", "")
        env["__MCP_SCRIPT"] = str(script_path)
        env["__MCP_URL"] = url or ""
        env["__MCP_NAV_TIMEOUT"] = str(nav_ms)
        env["__MCP_MARKER"] = _RESULT_MARKER

        proc = subprocess.Popen(  # noqa: S603 - фіксований бінарник node + наш гарнес
            [node, str(harness_path)],
            cwd=str(workdir) if workdir else str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,  # нова група процесів — вбиваємо все дерево за таймаутом
            preexec_fn=preexec_as_user,
        )
        try:
            out, err = proc.communicate(timeout=timeout + 10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()
            out, err = proc.communicate()
            payload, _ = _split_result(out or "")
            result = payload if payload else {"ok": False, "error": f"browser run timed out after {timeout}s"}
            result["timed_out"] = True
            result["returncode"] = proc.returncode
            return result

        payload, output = _split_result(out or "")
        output = (output or "").strip()
        if payload is None:
            # Гарнес не встиг/не зміг надрукувати результат (аварійний краш Chrome тощо).
            return {
                "ok": False,
                "error": (err or "").strip() or f"no result marker; node exit={proc.returncode}",
                "returncode": proc.returncode,
                "output": output[-MAX_READ_BYTES:],
            }
        payload["returncode"] = proc.returncode
        payload["timed_out"] = False
        if output:
            payload["output"] = output[-MAX_READ_BYTES:]
        elif err and not payload.get("ok"):
            payload["stderr"] = (err or "").strip()[-MAX_READ_BYTES:]
        return payload
    finally:
        for p in (harness_path, script_path):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
