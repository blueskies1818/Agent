# MCP Integration

The agent's tool system is built on the **Model Context Protocol (MCP)** — an open standard for LLM tool communication using JSON-RPC 2.0 and JSON Schema (spec 2025-06-18+).

This makes the agent's tools interoperable with any MCP-compatible client (Claude Code, Cursor, OpenClaw, etc.) and lets external MCP servers be plugged in with zero glue code.

---

## Standards alignment

| Area | Standard | This implementation |
|------|----------|---------------------|
| Protocol | MCP spec 2025-06-18+ | `mcp>=1.2.0` (Anthropic Python SDK) |
| Server library | FastMCP 3.x | `fastmcp>=3.0` |
| In-process transport | `Client(server_instance)` | Built-in server passed directly — no subprocess |
| External transport | stdio (subprocess), HTTP | Both supported in `MCPClient` |
| Tool naming | `snake_case`, `verb_noun`, 1–64 chars | `search_web`, `run_shell`, `memory`, etc. |
| Tool namespacing | forward-slash hierarchy | Optional — e.g. `memory/query` for future structured sub-tools |
| Claude Code compat | MCP client, stdio transport | Built-in server exposable via `mcp_servers/serve.py` |
| OpenClaw compat | MCP bridge, OAuth2 | Same protocol — no extra work needed |

---

## Architecture

```
Agent shell command
  search_web -query "best pizza" -sources 5
          │
  engine/nodes.py: _run_shell(command)
          │
  engine/mcp_router.py: MCPRouter.try_handle()
    ├─ parse_command()   → name="search_web", raw_args="-query ..."
    ├─ registry lookup   → found → MCPClient
    ├─ _build_call_args() → {"args": "-query \"best pizza\" -sources 5"}
    └─ client.call_tool("search_web", {...})  [JSON-RPC over in-process transport]
          │
  mcp_servers/web_tools.py: search_web(args=...)
    └─ calls mods/web_search/web_search.py handle()
          │
  ModResult(text=..., attachments=[...])
          │
  engine/nodes.py: actor continues

  — if first token NOT in registry → run_command() in sandbox (unchanged)
```

The agent output format is unchanged — it writes shell commands. MCP is the dispatch layer underneath.

---

## Key files

| File | Role |
|------|------|
| `engine/mcp_router.py` | `MCPRouter` — aggregates all servers, dispatches `try_handle()` |
| `engine/mcp_client.py` | `MCPClient` — async client for one server (in-process/HTTP/stdio) |
| `engine/cli_parser.py` | `parse_command()` — converts CLI flag strings to `(name, raw_args, dict)` |
| `mcp_servers/__init__.py` | Assembles built-in FastMCP server (`agent-builtin`) |
| `mcp_servers/shell_tools.py` | `run_shell`, `read_file`, `write_file` |
| `mcp_servers/memory_tools.py` | `memory` |
| `mcp_servers/web_tools.py` | `search_web` |
| `mcp_servers/ui_tools.py` | `debug_ui` |
| `mcp_servers/schedule_tools.py` | `schedule` |
| `mcp_servers/passwd_tools.py` | `passwd` |
| `mcp_servers/vault_tools.py` | `vault` |
| `mcp_config.json` | User-editable list of external servers |
| `config.py` | `MCP_CONFIG_FILE`, `MCP_BUILTIN_ENABLED` |

---

## Transport

### In-process (built-in tools)

Built-in tools run inside the same Python process using FastMCP's in-process transport:

```python
from fastmcp import Client, FastMCP

server = FastMCP("agent-builtin")

@server.tool
def search_web(args: str = "") -> str:
    ...

async with Client(server) as client:
    result = await client.call_tool("search_web", {"args": "-query foo"})
```

No subprocess, no network. Each call opens and closes an in-process session.

### Stdio (external subprocess)

External servers launched as subprocesses:

```json
{"name": "filesystem", "transport": "stdio",
 "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
```

### HTTP (remote server)

```json
{"name": "my-remote", "transport": "http", "url": "http://localhost:8080/mcp"}
```

---

## External server configuration

Edit `mcp_config.json` to add external servers. They are connected at session startup alongside the built-in server.

```json
{
  "servers": [
    {
      "name": "filesystem",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    },
    {
      "name": "github",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxxx"}
    }
  ]
}
```

Once connected, the external server's tools appear in `mod_index()` and are callable with the same shell-command syntax the agent already uses. The agent writes:

```
filesystem_list_dir -path /workspace
```

and `MCPRouter` dispatches it to the `filesystem` server's `list_dir` tool with `{"path": "/workspace"}`.

---

## Arg conversion

`engine/cli_parser.py` converts shell-style flags to dicts:

```
"-query \"best pizza\" -sources 5"
→ {"query": "best pizza", "sources": "5"}

"-read"
→ {"read": True}

"-register foo.md my_skill"
→ {"register": True, "_args": ["foo.md", "my_skill"]}
```

`MCPRouter._build_call_args()` checks the tool's JSON Schema:
- Single `args: string` parameter (all built-in tools) → passes the raw args string unchanged
- Structured parameters (external tools) → passes the parsed dict

String values are passed as strings; the MCP server (FastMCP) coerces them to typed values via the input schema.

---

## Tool schema (JSON Schema)

FastMCP 3.x auto-generates JSON Schema from Python type hints. Example:

```python
@mcp.tool
def search_web(args: str = "") -> str:
    """Search the internet. Args: -query "terms" [-sources N] | -url "https://..."."""
    ...
```

Generates:
```json
{
  "name": "search_web",
  "description": "Search the internet. Args: -query \"terms\" [-sources N] | -url \"https://...\".",
  "inputSchema": {
    "type": "object",
    "properties": {
      "args": {"type": "string", "default": ""}
    }
  }
}
```

---

## Claude Code integration

The built-in server can be exposed over stdio so Claude Code can connect to it:

```bash
# Add to Claude Code's MCP config
claude mcp add agent-tools -- python -m mcp_servers.serve
```

Once connected, all built-in tools appear in Claude Code's `@` autocomplete and can be called directly from the Claude Code REPL.

A `mcp_servers/serve.py` entry point can be added to support this:

```python
# mcp_servers/serve.py
from mcp_servers import get_builtin_server

if __name__ == "__main__":
    server = get_builtin_server()
    server.run()   # FastMCP 3.x stdio server
```

---

## MCPRouter lifecycle

```
AgentLoop.__init__()
  └─ MCPRouter()              # creates background event loop thread
  └─ connect_all()            # connects built-in + mcp_config.json servers
        ├─ MCPClient("inprocess", server=builtin_fastmcp)
        │    └─ list_tools() → registry["search_web"] = (client, ToolDef(...))
        └─ MCPClient("stdio", ...) for each external entry
             └─ list_tools() → registry["filesystem_list_dir"] = (client, ToolDef(...))

AgentLoop.run(user_input)
  └─ graph → actor → _run_shell(cmd) → MCPRouter.try_handle(cmd)
        └─ asyncio.run_coroutine_threadsafe(client.call_tool(...), background_loop)
        └─ future.result(timeout=120)

AgentLoop.close()
  └─ MCPRouter.shutdown()     # stops background event loop thread
```

---

## Adding a new external-style tool (structured params)

If you want a tool with typed parameters rather than a raw `args: str`:

```python
# mcp_servers/my_tools.py
def register_tools(mcp) -> None:
    @mcp.tool
    def lookup_user(user_id: str, include_history: bool = False) -> str:
        """Look up a user by ID."""
        ...
```

The agent writes:
```
lookup_user -user_id alice -include_history
```

`parse_command` produces `{"user_id": "alice", "include_history": True}`, which is passed directly to the tool because the schema has more than one property.

---

## Verification

Quick smoke-tests (run from project root after `pip install -r requirements.txt`):

```bash
# 1. Router boots, connects, lists tools
python -c "
from engine.mcp_router import MCPRouter
r = MCPRouter()
r.connect_all()
print(r.mod_index())
r.shutdown()
"

# 2. End-to-end tool interception
python -c "
from engine.mcp_router import MCPRouter
r = MCPRouter()
r.connect_all()
hit, result = r.try_handle('search_web -query \"test query\"')
print(hit, result.text[:200])
r.shutdown()
"

# 3. Sandbox fallthrough (unknown command should not match any tool)
python -c "
from engine.mcp_router import MCPRouter
r = MCPRouter()
r.connect_all()
hit, _ = r.try_handle('ls -la /tmp')
print('hit:', hit)   # expected: False
r.shutdown()
"

# 4. External server — add a stdio entry to mcp_config.json, then:
#    python -c "from engine.mcp_router import MCPRouter; r = MCPRouter(); r.connect_all(); print(r.mod_index()); r.shutdown()"
#    The new server's tools should appear in the index.

# 5. Claude Code integration
#    claude mcp add agent-tools -- python -m mcp_servers.serve
#    Then open claude — tools appear in @ autocomplete.
```

---

## Dependencies

```
fastmcp>=3.0    # High-level MCP server/client (Anthropic ecosystem)
mcp>=1.2.0      # Official MCP Python SDK (protocol primitives, StdioServerParameters)
```

Both are in `requirements.txt`.


[[overview]]
