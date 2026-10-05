# Filesystem MCP Server (для ChatGPT)

MCP-сервер на Python (офіційний SDK `mcp` 2.x), який дає моделі керувати
файловою системою через протокол **Streamable HTTP**. Інструменти:

| Інструмент | Призначення |
|---|---|
| `search_files` | Пошук за іменем файлу та (за бажанням) за вмістом |
| `read_file` | Читання тексту, зокрема за діапазоном рядків |
| `write_file` | Створення / перезапис / дозапис файлу |
| `update_file` | Цільова заміна (find-and-replace), зокрема за regex |
| `execute_command` | Виконання команд із захопленням stdout/stderr |
| `list_directory` | Перегляд вмісту каталогу |
| `delete_path` | Видалення файлу або каталогу (каталог — лише порожній або з `recursive=True`) |
| `rename_path` | Перейменування/переміщення файлу або каталогу (захист від перезапису без `overwrite=True`) |
| `test_in_browser` | Виконання Puppeteer-сценарію у headless Chrome (Node.js) із доступним `page` і повертання результату |

> ⚠️ Типово сервер надає доступ **до всієї файлової системи** і може
> виконувати довільні команди. Це потужно й небезпечно. Для підключення до
> ChatGPT обов'язково обмежте доступ (`--root`) та/або поставте сервер за
> автентифікований reverse proxy / tunnel.

## Швидкий старт

```bash
pip install -r requirements.txt
python3 server.py
# [filesystem-mcp] bind = http://0.0.0.0:8222/llm/mcp/{user}
```

Перевірка (локальний MCP-клієнт зі SDK):

```bash
python3 client_test.py
```

## Конфігурація (аргументи командного рядка)

Усі параметри задаються прапорцями під час запуску (змінні середовища не
використовуються). Актуальний список — `python3 server.py --help`.

| Прапорець | Типово | Опис |
|---|---|---|
| `--host` | `0.0.0.0` | Адреса прослуховування |
| `--port` | `8222` | Порт |
| `--path` | `/llm/mcp/{user}` | Шлях ендпоінта Streamable HTTP |
| `--root` | *(порожньо)* | Якщо задано — «пісочниця», усі шляхи обмежені цією папкою |
| `--allowed-hosts` | *(порожньо)* | Список допустимих `Host` через кому; вмикає DNS-rebinding protection. Порожньо або `*` — protection вимкнено |
| `--max-read-bytes` | `5242880` | Ліміт на розмір читаного/писаного файлу |
| `--max-search-results` | `500` | Стеля кількості результатів пошуку/перегляду |
| `--exec-timeout` / `--exec-timeout-max` | `60` / `600` | Таймаут виконання команд (типово / максимум), сек |

Приклади:

```bash
python3 server.py --host 127.0.0.1 --port 8222 --path /llm/mcp/{user}
python3 server.py --root /srv/agent                 # обмежити все пісочницею
python3 server.py --allowed-hosts a.example.com,b.example.com
```

## Підключення до ChatGPT

ChatGPT (функція **Connectors / Developer Mode**) підключається лише до
**віддалених** MCP-серверів за публічним **HTTPS** URL. Локальний `localhost`
безпосередньо не підходить — потрібен тунель в інтернет.

1. **Запустіть сервер** (найкраще в пісочниці):
   ```bash
   python3 server.py --root /srv/agent
   ```

2. **Пробросьте його в інтернет** за HTTPS, наприклад через Cloudflare Tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:8222
   # видасть URL вигляду https://xxxx.trycloudflare.com
   ```
   (аналогічно працює `ngrok http 8222`).

3. **Дозвольте зовнішній Host** — інакше DNS-rebinding protection відхилить запити.
   Додайте домен тунелю:
   ```bash
   python3 server.py --root /srv/agent --allowed-hosts xxxx.trycloudflare.com
   ```

4. **У ChatGPT**: Settings → Connectors (або GPT з **Developer Mode**) →
   **Add / Import MCP server** → вкажіть URL сервера:
   ```
   https://xxxx.trycloudflare.com/llm/mcp
   ```
   Коли з'явиться підтвердження, дозвольте доступ і увімкніть сервер у чаті.
   Далі можна писати, наприклад: «знайди всі `.log` у /srv/agent і покажи,
   де трапляється помилка OOM» — ChatGPT викличе `search_files`.

### Автентифікація (рекомендується для продакшену)
Публічний тунель без авторизації = публічний доступ до вашої ФС. Варіанти:
- тримати пісочницю (`--root`) і лише read-mostly операції;
- поставити за reverse proxy (Caddy/Nginx) з mTLS / API-ключем / OAuth;
- SDK підтримує OAuth-провайдера та `token_verifier` у конструкторі `MCPServer`
  для повноцінної авторизації.

## Бойовий запуск: screen + reverse proxy (nginx)

Сервер тримають у від'єднаній `screen`-сесії `mcp` і виводять наружу через
nginx з TLS на існуючому сайті (сервер слухає лише `127.0.0.1`, тому публічний
доступ іде через HTTPS-реверс-проксі на 443).

Запуск у screen (лог — у `server.log`):

```bash
screen -dmS mcp bash -lc "cd /opt/mcpserver && exec /usr/local/.pyenv/versions/3.13.7/bin/python3 server.py --host 127.0.0.1 --port 8222 --path /llm/mcp >> server.log 2>&1"
```

Перезапуск: `screen -S mcp -X quit`, потім знову команда вище. Перегляд сесій —
`screen -ls`, підключення до логу — `screen -r mcp` (вийти: `Ctrl-a d`).

Блок `location` для nginx (всередину чинного `server { ... listen 443 ssl http2; }`):

```nginx
location ~ "^/llm/mcp/(?<mcp_user>[A-Za-z_][A-Za-z0-9_.-]{0,31})/?$" {
    proxy_pass http://127.0.0.1:8222;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-MCP-User $mcp_user;
    proxy_set_header Connection "";

    # Streamable HTTP / SSE: без буферизації, довгі таймаути, без кешу/стиснення
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_cache off;
    gzip off;
    chunked_transfer_encoding on;
    proxy_read_timeout 7200s;
    proxy_send_timeout 7200s;

    client_max_body_size 10m;
}
```

Після правки — `nginx -t && nginx -s reload`.

## Структура вихідного коду та примітки

(зведення перенесено з докстрингів і коментарів модулів `*.py`)

### Точка входу — `server.py`

Розбирає аргументи командного рядка, застосовує конфігурацію та запускає
Streamable HTTP.

- Порядок важливий: `config.configure()` викликається **до** імпорту `fs_mcp.tools`, бо
  модулі інструментів зчитують ліміти з `config` у момент імпорту (дефолти
  сигнатур і константи).
- Каталог самого файлу додається в `sys.path`, щоб пакет `fs_mcp`
  імпортувався незалежно від того, з якого каталогу запущено сервер
  (абсолютний шлях, симлінк, `cron`); `resolve()` розгортає симлінки.
- DNS-rebinding protection вмикається, якщо задано `--allowed-hosts` (не `*`);
  інакше захист вимкнено — це потрібно для доступу через зовнішній тунель/домен
  (наприклад, ChatGPT). Бажано ставити сервер за reverse proxy.

### Пакет `fs_mcp`

- `config.py` — конфігурація сервера і розв'язання шляхів. Значення задаються через
  `configure()` з аргументів командного рядка; в модулі оголошено лише дефолти.
  Є необов'язкова «пісочниця» (`SANDBOX`): якщо задана, кожен шлях має
  резолвитися всередину неї, інакше доступна вся файлова система.
  Відносні шляхи та префікс `~`/`~/` у пошуку/листингу/читанні/записі та як
  робочий каталог `execute_command` рахуються від домашнього каталогу
  авторизованого MCP-користувача (`pw_dir`); якщо задано пісочницю — від неї.
- `app.py` — екземпляр `MCPServer` (синглтон), на який навішуються інструменти.
- `audit.py` — аудит викликів інструментів. Підключається middleware до `MCPServer`
  (`server.middleware.append(...)` у `server.py`) і пише деталі кожного
  `tools/call` у stderr (перехоплюється `server.log` через `2>&1`) окремим
  логгером із `propagate=False`, щоб не залежати від налаштувань uvicorn і не
  дублювати записи. На кожен виклик приходять рядки `tool_call start` (ім'я та
  аргументи; довгі поля на кшталт `content` скорочуються до `_MAX_STR` символів,
  байтові — до `<bytes Nb>`, щоб не забивати журнал і не світити величезні
  payload'и) і `tool_call done` (тривалість та підсумок); у разі невдачі рівень
  `WARNING`/`ERROR` і текст помилки. Інші методи (`initialize`, `tools/list`,
  нотифікації) не логується — пишеться лише `tools/call`.
- `syntax.py` — перевірка синтаксису коду, що створюється/редагується.
  Підтримуються `.py` (через вбудований `ast`), `.js/.mjs/.cjs` (через
  `node --check`) та `.php` (через `php -l`). Для JS/PHP вміст записується у
  тимчасовий файл із потрібним розширенням і запускається відповідний
  інтерпретатор; шлях тимчасового файлу в повідомленнях замінюється на `<content>`.
  Якщо потрібного бінарника немає в `PATH`, перевірка мовчки пропускається (файл не
  блокується), щоб відсутність лінтера не ламала запис.
- `tools/` — реалізація інструментів, по одному файлу на групу. Імпорт модулів
  у `tools/__init__.py` реєструє їх на `MCPServer` (порядок імпорту не важливий —
  кожен модуль сам навішує `@server.tool()`).

### Інструменти (`fs_mcp/tools/*`)

| Модуль | Призначення |
|---|---|
| `listing.py` | перегляд каталогів |
| `search.py` | пошук файлів за іменем і вмістом |
| `read.py` | читання файлів |
| `write.py` | запис файлів |
| `update.py` | цільове редагування (find-and-replace) |
| `execute.py` | виконання команд |
| `remove.py` | видалення файлів і каталогів |
| `rename.py` | перейменування/переміщення файлів і каталогів |
| `browser.py` | запуск Puppeteer-сценарію у headless Chrome через Node.js (`test_in_browser`) |

## Інші файли
- `client_test.py` — тестовий MCP-клієнт (проганяє всі інструменти).
- `requirements.txt` — залежності.
