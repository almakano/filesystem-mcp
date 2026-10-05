from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer(
    name="filesystem-mcp",
    instructions=(
        "Filesystem management server. Use search_files to locate content, "
        "read_file to inspect, write_file to create/overwrite, update_file for "
        "targeted find-and-replace edits, execute_command to run programs, and "
        "list_directory to browse. Paths may be absolute, or relative (including \"~\" \"~/...\") "
        "against the authenticated MCP user's home directory. Every tool also accepts an "
        "optional `cwd` (default: the user's home) that acts as the base for relative paths."
    ),
)
