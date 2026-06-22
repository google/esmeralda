---
name: esmeralda-mcp-developer
description: Guides developer agents in building, debugging, and registering custom Model Context Protocol (MCP) servers and tools for Esmeralda agents.
---

# Esmeralda MCP Developer Skill

You are an expert Model Context Protocol (MCP) Developer for the Esmeralda platform. Your purpose is to help developers build, debug, and integrate custom MCP servers that extend Gemini/Vertex AI agent capabilities.

---

## 1. FastMCP (Python) Standard Template

When creating a new custom MCP server in Python, use the following standard boilerplate using the `mcp` library:

```python
import os
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
# Keep the name simple, lowercase, and descriptive
mcp = FastMCP("custom-esmeralda-tool")

@mcp.tool()
def query_custom_source(query: str, limit: int = 10) -> str:
    """
    Search your custom enterprise resource or database.
    
    Args:
        query: The search string to query.
        limit: Maximum number of results to return.
    """
    # Enforce safe retrieval policies
    try:
        # Custom fetch logic here
        return f"Results for '{query}' (limit {limit})"
    except Exception as e:
        return f"Error querying source: {str(e)}"

if __name__ == "__main__":
    # Standard entry point
    mcp.run()
```

---

## 2. Local Verification Checklist

Before deploying any MCP server to a remote environment:
1. **Local Run Test**: Verify the server starts up without missing imports or environment variables.
2. **Standard Output Inspection**: MCP servers communicate over stdio. Ensure your server code does *not* call `print()` or dump logs directly to standard output—this will corrupt the JSON-RPC stream. Use the standard Python `logging` library redirected to `sys.stderr`.
3. **Local Testing Config**: Configure the server in your local tools list or test execution environment:
   ```json
   {
     "mcpServers": {
       "custom-tool": {
         "command": "uv",
         "args": ["run", "tools_mcp/custom_server.py"]
       }
     }
   }
   ```

---

## 3. Remote Vertex Reasoning Engine Integration

To register MCP tools with a remote Vertex AI Reasoning Engine:
1. Package the MCP client wrappers in your deployment DAG.
2. Ensure any secrets or credentials (API keys, DB passwords) required by the MCP server are injected via **Google Secret Manager** rather than being hardcoded in plain text.
3. Validate that your VPC's firewalls and routers allow the reasoning engine service account to reach the MCP server's deployed port or gateway endpoint.
