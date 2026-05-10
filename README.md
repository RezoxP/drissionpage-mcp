# DrissionPage MCP

Powerful browser automation for MCP clients, built on [DrissionPage](https://github.com/g1879/DrissionPage).

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

## Quick install

The easiest setup is `uvx`, because MCP clients can install and run the server from GitHub
without manually cloning this repository.

```json
{
  "mcpServers": {
    "drissionpage": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/RezoxP/drissionpage-mcp",
        "drissionpage-mcp"
      ]
    }
  }
}
```

If Chrome is not installed or you want Thorium/Chromium/Edge/Brave instead, pass the browser
binary as a separate argument:

```json
{
  "mcpServers": {
    "drissionpage": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/RezoxP/drissionpage-mcp",
        "drissionpage-mcp",
        "--browser-binary",
        "C:\\Users\\Rohan\\AppData\\Local\\Thorium\\Application\\thorium.exe"
      ]
    }
  }
}
```

Do not put `uv run drissionpage-mcp --browser-binary ...` into the MCP `command` field as one
string. MCP clients expect `command` and each argument to be split exactly like the JSON above.

To generate config and check your browser path before adding it to an MCP client:

```bash
uvx --from git+https://github.com/RezoxP/drissionpage-mcp drissionpage-mcp --doctor --browser-binary "C:\Users\Rohan\AppData\Local\Thorium\Application\thorium.exe"
```

For a local checkout, use:

```json
{
  "mcpServers": {
    "drissionpage": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\path\\to\\drissionpage-mcp",
        "run",
        "drissionpage-mcp",
        "--browser-binary",
        "C:\\Users\\Rohan\\AppData\\Local\\Thorium\\Application\\thorium.exe"
      ]
    }
  }
}
```

## Package install

```bash
pip install drissionpage-mcp
```

For local development:

```bash
uv sync --extra dev
uv run drissionpage-mcp
```

## MCP configuration after package install

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

Troubleshooting:

- `transport closed` usually means the MCP command exited before the client initialized it.
  Run `drissionpage-mcp --doctor` outside the MCP client first.
- On Windows, browser paths with backslashes must be inside one JSON string.
- Use `--print-config --browser-binary "..."` to print a ready-to-copy config.

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
- Install/debug help: `install_help`
- Navigation: `page_navigate`, `page_back`, `page_forward`, `page_refresh`, `page_info`, `wait`
- Context: `page_snapshot`, `snapshot_read`, `page_text`, `element_find`
- Interaction: `element_click`, `element_type`, `element_select`, `element_hover`, `element_drag`, `mouse_click_xy`, `keyboard_press`
- Power tools: `js_eval`, `cdp_send`, `network_start`, `network_stop`, `network_logs`
- Files/session: `page_screenshot`, `download_file`, `upload_file`, `cookies_get`, `session_info`
