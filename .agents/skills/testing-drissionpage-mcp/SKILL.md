---
name: testing-drissionpage-mcp
description: Test the DrissionPage MCP server browser tools end-to-end. Use when verifying navigation snapshots, element_actions, screenshots, dialogs, JS errors, or network_logs.
---

# DrissionPage MCP Testing

## Devin Secrets Needed

None. The core smoke workflow can run entirely locally with a generated HTML page and local Chromium.

## Setup

1. Install/sync dependencies:
   ```bash
   uv sync --extra dev
   ```
2. Run static checks before browser smoke testing:
   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run python -m py_compile src/drissionpage_mcp/*.py
   uv run drissionpage-mcp --doctor
   ```
3. `uv run pytest` may exit 5 if the repo has no tests under the configured `tests` path; do not treat that as a runtime browser smoke result.

## Browser launch notes

- `/home/ubuntu/.local/bin/google-chrome` may be a Devin URL-forwarding wrapper rather than a standalone Chrome binary. It can fail when DrissionPage tries to launch arbitrary remote-debugging ports.
- Prefer launching the real bundled Chromium binary directly when available, e.g. `/opt/.devin/chrome/chrome/linux-137.0.7118.2/chrome-linux64/chrome`, through `browser_start_or_connect(browser_binary=..., port=<free_port>, user_data_dir=<isolated_profile>)`.
- Use an isolated profile under the repo or another persistent workspace path for smoke tests; do not reuse the active user profile.
- For visible recordings on Linux, maximize the browser with `wmctrl -r :ACTIVE: -b add,maximized_vert,maximized_horz` if `wmctrl` is installed.

## Smoke test shape

Use a local HTML page with:
- input `#name` with an `input` listener that mirrors text into an output,
- select `#choice` with an option value `beta`,
- button `#fetch` that calls `/api/data`,
- a static JS asset under `/static/app.js`.

Exercise the actual FastMCP tool functions from `create_app()._tool_manager` and verify:

1. `page_navigate(local_url, snapshot=True)` returns `status="navigated"`, `ready.network_idle=true`, `pending_requests=0`, and refs for the page controls.
2. `element_actions` with deterministic CSS targets (`#name`, `#choice`) returns `status="ok"`, `page_changed=true`, and `changed_elements` containing input `value="Ada"` and select `value="beta"`.
3. `page_screenshot(as_base64=True)` converts through FastMCP content conversion to an image block with `mimeType="image/png"`.
4. `js_eval("const = ;")` returns `ok=false`, `error_kind="syntax"`, and a snippet containing `const = ;`.
5. Trigger dialogs directly for `alert_dismiss` testing, e.g. schedule `alert("Blocking alert")` with `cdp_send("Runtime.evaluate", ...)`, then assert `alert_dismiss(accept=False)` returns `status="handled"` and `text="Blocking alert"`.
6. Click `#fetch` with `element_actions(by="css")`, then verify `network_logs(view="fetch/xhr")` contains `/api/data` with status `200` and `network_logs(view="all")` includes a `/static` group count.

Avoid using broad `element_find` text results for deterministic smoke actions; it can return generated refs that are valid but not the intended control for a narrow assertion. Use snapshot refs when proving ref behavior, and explicit CSS selectors when proving action semantics.
