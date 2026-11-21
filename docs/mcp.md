# AI assistants (MCP)

AI assistants can see the printers and the saved labels and print through the [Model Context Protocol](https://modelcontextprotocol.io) at `/mcp`, e.g. `http://labels.local:8013/mcp`: "print a label for the jar of chili, today's date".

The endpoint has no login, like the API. Keep it inside your home network, never make it reachable from the internet.

It needs the packages of `requirements-mcp.txt`, which the Docker image comes with, except on 32-bit ARM, where some of them would have to be built with Rust. Elsewhere:

```bash
pip install -r requirements-mcp.txt
```

`MCP_ENABLED=false` turns the endpoint off.

## Tools

| Tool | What it does |
|---|---|
| `list_printers` | The printers with their alias, device, driver, the maker, model and serial number they report on USB, status, errors and the label size loaded |
| `list_labels` | The saved labels with their label size, tags, what they say and their blanks |
| `preview_label` | The image of a saved label with its blanks filled in, or of lines of text and a code, e.g. a QR code, DataMatrix code or barcode |
| `print_label` | Prints a saved label, also once for each of several fillings of its blanks, e.g. 12 name badges from a list |
| `print_text` | Prints lines of text and a code, e.g. a QR code of a URL or a Wi-Fi network, a DataMatrix code or a barcode |

The tools take a printer by any of these names or a part of one, e.g. "Datamax" or its serial number, and refuse one that names several printers.

Printing makes paper, so both print tools:

- need the number of labels and print at most `MCP_MAX_COUNT` (default 10) per call, counting each label, not the calls,
- need the printer, and take the label size it has loaded, else the one set in its printer settings. Where neither tells or it may have several loaded, they print nothing and tell the assistant to ask the user. A label size given is refused if the printer has another one loaded or set,
- check every label before the first is printed, and print none where one is refused or the label designer warns about it, e.g. text cut off, a blank the label does not have or a date blank without a date. A font that is not installed is the exception: the label prints with the fallback font, as in the label designer, and the result says so,
- count each page of a saved PDF as a label,
- return the image of what they added to the queue, and its address.

Like every print, they are answered once the labels are in the queue of the printer, not when they are printed. A label that fails to print is told at `GET /labeldesigner/api/queue/<id>` and in the label designer. Errors are those of the API, e.g. "EAN-13 takes 12 digits, or 13 with the check digit". Claude Code asks before each print, also in its auto mode, other clients as they are set up.

## Clients

### Claude Code

```bash
claude mcp add --transport http labelburg http://labels.local:8013/mcp
```

### VS Code

In `.vscode/mcp.json`:

```json
{
  "servers": {
    "labelburg": {"type": "http", "url": "http://labels.local:8013/mcp"}
  }
}
```

### Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "labelburg": {"url": "http://labels.local:8013/mcp"}
  }
}
```

### Gemini CLI

```bash
gemini mcp add --transport http labelburg http://labels.local:8013/mcp
```

### Codex CLI

```bash
codex mcp add labelburg --url http://labels.local:8013/mcp
```

### Zed

In its `settings.json`:

```json
"context_servers": {
  "labelburg": {"url": "http://labels.local:8013/mcp"}
}
```

### LM Studio

In `mcp.json` (Program, Install, Edit mcp.json):

```json
{
  "mcpServers": {
    "labelburg": {"url": "http://labels.local:8013/mcp"}
  }
}
```

### Open WebUI

In the admin settings, Integrations, External Tool Servers: add a connection of the type **MCP (Streamable HTTP)** with the URL of the endpoint. Open WebUI 0.6.31 or later.

### Home Assistant

Add the integration **Model Context Protocol** with the URL of the endpoint, then turn on its tools for the conversation agent of your assistant. Home Assistant uses only tools and no login here. It does not ask before printing, `MCP_MAX_COUNT` limits how many labels a call prints. It hands each result to its model as text, so it gets only the address of each image, below `/mcp/preview.png`, not the image data. Opened over HTTPS, Home Assistant shows them only if the label designer is reachable over HTTPS too, browsers block images over HTTP there.

### claude.ai, Claude Desktop, ChatGPT

Their connectors connect from the cloud of Anthropic or OpenAI, which cannot reach a server in your home network.

## Host names

Like the label designer, the endpoint answers only requests for the names of the home network and those of `HOSTS`, also in `Origin`, see [Host names](../README.md#host-names). That protects against DNS rebinding: a website that points its own name at your server cannot use it. Other names go into `HOSTS`, e.g., `HOSTS=labels.example.com`, or `.example.com` for all its subdomains. `MCP_HOSTS`, its former name, still works. With `TRUSTED_HOSTS` set, it answers exactly its names.

Behind a [reverse proxy](reverse-proxy.md) the endpoint is below the path of the label designer, e.g. `https://labels.example.com/labels/mcp`.
