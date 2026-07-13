# Mythify MCP server

This package is the optional Node MCP runtime for Mythify. It exposes the same
`.mythify` state as the Python CLI and adds MCP tools, including fanout.

The GitHub release tarball is installed as a local package, not from a public
npm registry:

```sh
mkdir mythify-mcp-runtime
cd mythify-mcp-runtime
npm init -y
npm install /path/to/mythify-mcp-4.3.0.tgz
```

Configure an MCP client to run:

```text
node /absolute/path/to/mythify-mcp-runtime/node_modules/mythify-mcp/src/index.js
```

Set `MYTHIFY_DIR` to the target project's `.mythify` directory. Node 20 or
newer is required. The package is MIT licensed.
