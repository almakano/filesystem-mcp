from __future__ import annotations

import json
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from ..app import server
from ..auth import get_user, preexec_as_user
from ..config import EXEC_TIMEOUT_MAX, MAX_READ_BYTES, resolve_path

# Типовий реальний ліміт усього запуску браузера (сек).
DEFAULT_BROWSER_TIMEOUT = 60.0
# Маркер, яким гарнес відділяє итоговий JSON-результат від решти stdout.
_RESULT_MARKER = "__MCP_BROWSER_RESULT__"

# Типовий «валідний» desktop Chrome UA. Без нього headless Chromium надсилає
# «HeadlessChrome», що багато сайтів/фільтрів блокують. Можна перевизначити
# параметром `user_agent` інструмента або змінною MCP_PLAYWRIGHT_USER_AGENT.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# Гарнес: запускає тіло сценарію як функцію, якій доступні
# `page`, `browser`, `context` і `playwright`, і печатает результат після маркера.
_HARNESS = r"""
import json
import os
import shutil
import sys
import textwrap
import traceback

MARKER = os.environ.get("__MCP_MARKER") or "__MCP_BROWSER_RESULT__"


def emit(payload):
    # Окремий рядок: \n + MARKER + <json> + \n, щоб надійно витягнути за маркером.
    sys.stdout.write("\n" + MARKER + json.dumps(payload) + "\n")
    sys.stdout.flush()


def serialize(value):
    if value is None:
        return None
    try:
        # Глибока копія через JSON: відсікає неserializable та нормалізує вивід.
        return json.loads(json.dumps(value))
    except Exception:
        try:
            return str(value)
        except Exception:
            return "[unserializable]"


def run():
    from playwright.sync_api import sync_playwright

    script_path = os.environ["__MCP_SCRIPT"]
    url = os.environ.get("__MCP_URL") or ""
    nav_timeout = int(os.environ.get("__MCP_NAV_TIMEOUT") or "30000")
    user_agent = os.environ.get("__MCP_UA") or ""
    authorization = os.environ.get("__MCP_AUTHORIZATION") or ""
    with open(script_path, "r", encoding="utf-8") as fh:
        user_script = fh.read()

    with sync_playwright() as playwright:
        # Браузер можна вказати через середовище, щоб не залежати від завантаження
        # вбудованого (bundled) Chromium із CDN Playwright (доступний лише для
        # підтримуваних дистрибутивів). Пріоритет:
        #   MCP_PLAYWRIGHT_EXECUTABLE — абсолютний шлях до системного бінарника
        #                              (chromium / google-chrome) [executable_path];
        #   MCP_PLAYWRIGHT_CHANNEL    — іменований канал Playwright ("chrome", "msedge");
        #   інакше — вбудований Chromium.
        #   MCP_PLAYWRIGHT_BROWSER    — движій: chromium (типово) | firefox | webkit.
        browser_name = (os.environ.get("MCP_PLAYWRIGHT_BROWSER") or "chromium").strip().lower()
        executable = (os.environ.get("MCP_PLAYWRIGHT_EXECUTABLE") or "").strip()
        channel = (os.environ.get("MCP_PLAYWRIGHT_CHANNEL") or "").strip()

        launcher = getattr(playwright, browser_name, None)
        if launcher is None:
            raise RuntimeError(f"unknown playwright browser: {browser_name}")

        base_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        launch_kwargs = {"headless": True, "args": base_args}
        if executable:
            launch_kwargs["executable_path"] = executable
        elif channel:
            launch_kwargs["channel"] = channel
        explicit = bool(executable or channel)

        try:
            browser = launcher.launch(**launch_kwargs)
        except Exception as err:
            # Без явного вказання движія та якщо вбудований браузер не завантажено
            # (актуально для Debian 11, де Playwright ≥1.62 не має власного білду,
            # а CDN може бути недоступний) — пробуємо системний Chromium/Chrome.
            fallback = None
            if not explicit and "executable doesn't exist" in str(err).lower():
                for cand in ("chromium", "chromium-browser", "chrome", "google-chrome", "google-chrome-stable"):
                    path = shutil.which(cand)
                    if not path:
                        continue
                    try:
                        browser = launcher.launch(headless=True, args=base_args, executable_path=path)
                        fallback = path
                        break
                    except Exception:
                        continue
            if fallback is None:
                raise
        try:
            # Створюємо контекст із валідним desktop Chrome UA (щоб headless
            # «HeadlessChrome» не блокувався сайтами) та відкриваємо сторінку в ньому.
            if user_agent:
                context = browser.new_context(user_agent=user_agent)
            else:
                context = browser.new_context()
            page = context.new_page()
            if authorization:
                page.set_extra_http_headers({"Authorization": authorization})
            page.set_default_timeout(nav_timeout)
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)

            # Тіло сценарію виконується як функція: доступні page/browser/context/playwright,
            # результат повертається через `return`. Обгортка потрібна, бо `return`
            # неможливий на верхньому рівні модуля.
            source = (
                "def __scenario__(page, browser, context, playwright):\n"
                + textwrap.indent(textwrap.dedent(user_script), "    ")
            )
            namespace = {}
            exec(compile(source, "<scenario>", "exec"), namespace)
            result = namespace["__scenario__"](page, browser, page.context, playwright)
            emit({"ok": True, "result": serialize(result)})
        finally:
            try:
                browser.close()
            except Exception:
                pass


try:
    run()
except Exception as err:
    emit({"ok": False, "error": "".join(traceback.format_exception(err))})
"""


def _playwright_available() -> bool:
    """Перевіряє, чи імпортується `playwright` інтерпретатором сервера (той самий,
    що запускає гарнес)."""
    return importlib.util.find_spec("playwright") is not None


def _split_result(stdout: str) -> tuple[dict | None, str]:
    """Розділює stdout гарнеса: (payload, чистий вивід сценарію).

    return: payload — розпарсений JSON, надрукований після маркера (або None, якщо
    маркера немає); output — усе, що було надруковано ДО маркера (print тощо),
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
def test_in_browser(script: str, url: str = "", timeout: float = DEFAULT_BROWSER_TIMEOUT, cwd: str = ".", user_agent: str = "", authorization: str = "") -> dict:
    """Виконує Python Playwright-сценарій у headless Chromium і повертає результат.

    `script` — тіло функції, якій доступні `page` (об'єкт Playwright Page),
    `browser`, `context` і `playwright`. Сценарій може робити `page.goto(...)` /
    `page.click(...)` / `page.evaluate(...)` тощо (синхронний API) і повертає
    значення через `return` (воно серіалізується в JSON). Якщо `url` задано, гарнес
    сам перейти на нього (wait_until='networkidle') перед запуском тіла; інакше
    навігація на совісті сценарію.

    Усі запити йдуть із валідним desktop Chrome User-Agent (типово
    `DEFAULT_USER_AGENT`), щоб headless-запити не блокувалися сайтами.

    Args:
        script: Тіло Python-сценарію з доступним `page`.
        url: Необов'язкова стартова адреса для `page.goto`.
        timeout: Загальний ліміт секунд на запуск; вкладається в межу exec-таймаута.
        cwd: Робочий каталог процесу; відносні шляхи у сценарії (напр. для
            скриншотів/файлів) розв'язуються від нього (типово — домашній каталог
            MCP-користувача).
        user_agent: Необов'язковий рядок User-Agent; порожньо — типовий Chrome UA.
        authorization: Необов'язане значення HTTP-заголовка Authorization (наприклад, `Bearer <token>`).

    Returns:
        dict з `ok` (bool); при успіху — `result` (повернуте сценарієм значення) і
        `output` (stdout сценарію, напр. print); при невдачі — `error`. Також
        додаються `timed_out`/`returncode` для діагностики.
    """
    if not script or not script.strip():
        raise ValueError("script must not be empty")

    # cwd ""/None → «.» → домашній каталог користувача (див. config.resolve_path).
    workdir = resolve_path(cwd or ".", access="traverse")

    if not _playwright_available():
        return {
            "ok": False,
            "error": "python-playwright не встановлено; виконайте "
                     "`pip install playwright && python -m playwright install chromium`",
        }

    # Загальний ліміт; грант +10s на старт/закриття Chromium, щоб JS-таймаут спрацював першим.
    timeout = min(max(1.0, timeout), EXEC_TIMEOUT_MAX)
    nav_ms = int(timeout * 1000)

    tmp_dir = Path(tempfile.mkdtemp(prefix="mcp_browser_"))
    # The harness runs after dropping privileges to the MCP user; let that user
    # traverse this per-run directory without exposing it to other local users.
    account = get_user()
    os.chown(tmp_dir, account.pw_uid, account.pw_gid)
    os.chmod(tmp_dir, 0o700)
    harness_path = tmp_dir / "harness.py"
    script_path = tmp_dir / "scenario.py"
    try:
        harness_path.write_text(_HARNESS, encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")

        env = dict(os.environ)
        env["__MCP_SCRIPT"] = str(script_path)
        env["__MCP_URL"] = url or ""
        env["__MCP_UA"] = (user_agent or env.get("MCP_PLAYWRIGHT_USER_AGENT") or DEFAULT_USER_AGENT).strip()
        env["__MCP_AUTHORIZATION"] = authorization or ""
        env["__MCP_NAV_TIMEOUT"] = str(nav_ms)
        env["__MCP_MARKER"] = _RESULT_MARKER

        proc = subprocess.Popen(  # noqa: S603 - фіксований інтерпретатор + наш гарнес
            [sys.executable, str(harness_path)],
            cwd=str(workdir) if workdir else str(Path.cwd()),
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
            # Гарнес не встиг/не зміг надрукувати результат (аварійний краш Chromium тощо).
            return {
                "ok": False,
                "error": (err or "").strip() or f"no result marker; python exit={proc.returncode}",
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
