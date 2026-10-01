import asyncio, json, os
from mcp import Client

from fs_mcp import config


def _default_url() -> str:
    """Збирає URL з конфігурації сервера (HOST/PORT/MOUNT_PATH).

    ``config.HOST`` — адреса прив'язки сервера; ``0.0.0.0``/``::`` не можна
    використовувати як ціль підключення, тому для клієнта вони мапляться в loopback.
    """
    host = config.HOST
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{config.PORT}{config.MOUNT_PATH}"


URL = os.environ.get("MCP_TEST_URL", _default_url())

BASE = "/tmp/mcp_demo_dir"
FILE = f"{BASE}/mcp_demo.txt"
RENAMED = f"{BASE}/mcp_demo_renamed.txt"


async def main():
    async with Client(URL) as c:
        tools = await c.list_tools()
        print("TOOLS:", [t.name for t in tools.tools])

        r = await c.call_tool("write_file", {"path": FILE, "content": "hello mcp\nline2\n"})
        print("WRITE:", r.content[0].text)

        r = await c.call_tool("read_file", {"path": FILE})
        print("READ:", json.loads(r.content[0].text)["content"].strip())

        r = await c.call_tool("update_file", {"path": FILE, "find": "hello", "replace": "HI"})
        print("UPDATE:", r.content[0].text)

        r = await c.call_tool("list_directory", {"path": BASE})
        listing = json.loads(r.content[0].text)
        print("LIST:", listing["count"], "entries in", listing["root"],
              "->", [os.path.basename(e["path"]) for e in listing["entries"]])

        r = await c.call_tool("rename_path", {"src": FILE, "dst": RENAMED})
        print("RENAME:", json.loads(r.content[0].text)["to"])

        r = await c.call_tool("search_files", {"pattern": "server", "root": "/opt/mcpserver"})
        print("SEARCH:", r.content[0].text[:160])

        r = await c.call_tool("execute_command", {"command": "echo executed && pwd", "shell": True})
        print("EXEC:", json.loads(r.content[0].text)["stdout"].strip())

        r = await c.call_tool("test_in_browser", {
            "script": "await page.setContent('<h1 id=t>hi mcp</h1>'); "
                      "const t = await page.$eval('#t', e => e.textContent); "
                      "return {text: t, upper: t.toUpperCase()};",
            "timeout": 30,
        })
        print("BROWSER:", json.loads(r.content[0].text)["result"])

        r = await c.call_tool("delete_path", {"path": RENAMED})
        print("DELETE file:", json.loads(r.content[0].text)["deleted"])

        r = await c.call_tool("delete_path", {"path": BASE, "recursive": True})
        print("DELETE dir:", json.loads(r.content[0].text)["deleted"])


asyncio.run(main())
