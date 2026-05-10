# DrissionPage MCP

Powerful browser automation for MCP clients, built on [DrissionPage](https://github.com/g1879/DrissionPage).

This server combines the strongest ideas from the three reviewed implementations while keeping responses context-friendly for AI agents:

| Implementation | Pros kept | Cons avoided |
| --- | --- | --- |
| `persist-1/DrissionPage-MCP-Server` | Broad tool coverage, modular browser/DOM/network concepts, screenshots, cookies, CDP, file helpers | Overly large dependency set, verbose responses, Windows-biased browser path handling |
| `jumodada/Drissionpage-MCP-Server` | Clean MCP shape, typed tool schemas, deterministic selector operations, simple install/run flow | Small tool surface and limited page/context capture |
| `wxhzhwxhzh/DrissionPageMCP` | Full DrissionPage power: CDP, keyboard, upload/download, hover/drag, response listeners, accessibility tree | Single-file structure, inconsistent names, typo-prone APIs, unbounded output |

## Highlights

- Browser lifecycle, tab switching, navigation, history, refresh, close.
- Context-friendly page snapshots with:
  - interactive element refs (`e1`, `e2`, ...)
  - generated XPath/CSS selectors
  - accessibility tree extraction via CDP
  - readable text truncation plus lossless `snapshot_read` pagination
- Element operations by ref, CSS, XPath, text, or role.
- Click, type, select, hover, drag, coordinate click, keyboard shortcuts.
- JavaScript and CDP escape hatches for full DrissionPage/Chrome potential.
- Network response listener with filters and bounded logs.
- Screenshots, cookies, file upload/download, browser/session diagnostics.

## Install

```bash
pip install drissionpage-mcp
```

For local development:

```bash
uv sync --extra dev
uv run drissionpage-mcp
```

## MCP configuration

```json
{
  "mcpServers": {
    "drissionpage": {
      "command": "drissionpage-mcp"
    }
  }
}
```

If Chrome is not installed or you want to use another Chromium-compatible browser:

```json
{
  "mcpServers": {
    "drissionpage": {
      "command": "drissionpage-mcp",
      "args": ["--browser-binary", "/path/to/chromium-or-edge"]
    }
  }
}
```

You can also set `DRISSIONPAGE_MCP_BROWSER_BINARY=/path/to/browser`. At runtime, call
`browser_find_binary` to see what binary the server will use.

## Recommended AI workflow

1. `browser_start_or_connect`
2. `page_navigate`
3. `page_snapshot`
4. Act with refs from the snapshot, for example `element_click(target="e3", by="ref")`
5. Use `snapshot_read` if the page is larger than the context window
6. Use `js_eval` or `cdp_send` only when normal tools are insufficient

## Tool groups

- Browser/tab: `browser_start_or_connect`, `browser_close`, `tab_new`, `tab_list`, `tab_activate`, `tab_close`
- Browser discovery: `browser_find_binary`
- Navigation: `page_navigate`, `page_back`, `page_forward`, `page_refresh`, `page_info`, `wait`
- Context: `page_snapshot`, `snapshot_read`, `page_text`, `element_find`
- Interaction: `element_click`, `element_type`, `element_select`, `element_hover`, `element_drag`, `mouse_click_xy`, `keyboard_press`
- Power tools: `js_eval`, `cdp_send`, `network_start`, `network_stop`, `network_logs`
- Files/session: `page_screenshot`, `download_file`, `upload_file`, `cookies_get`, `session_info`
