from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

INSTRUCTIONS = """
Use this DrissionPage MCP as a deterministic browser controller.
Recommended flow: start/connect browser, navigate, call page_snapshot, then act using element refs.
Snapshots are compact but lossless: if output is truncated, call snapshot_read with the returned snapshot_id.
Prefer refs from page_snapshot over guessed selectors. Use js_eval/cdp_send only when normal tools are insufficient.
"""

SAFE_KEY_NAMES = {
    "enter": "ENTER",
    "backspace": "BACKSPACE",
    "home": "HOME",
    "end": "END",
    "page_up": "PAGE_UP",
    "page_down": "PAGE_DOWN",
    "down": "DOWN",
    "up": "UP",
    "left": "LEFT",
    "right": "RIGHT",
    "esc": "ESCAPE",
    "escape": "ESCAPE",
    "ctrl+c": "CTRL_C",
    "ctrl+v": "CTRL_V",
    "ctrl+a": "CTRL_A",
    "delete": "DELETE",
}


def _now_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars] + f"\n… truncated {len(value) - max_chars} chars"


def _json(value: object, max_chars: int = 12000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return _truncate(text, max_chars)


def _clean_selector_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    joined = ", \"'\", ".join(f"'{part}'" for part in parts)
    return f"concat({joined})"


@dataclass
class SnapshotRecord:
    payload: str
    created_at: float
    source: str


@dataclass
class BrowserState:
    browser: object | None = None
    active_tab: object | None = None
    refs: dict[str, dict[str, str]] = field(default_factory=dict)
    snapshots: dict[str, SnapshotRecord] = field(default_factory=dict)
    network_events: list[dict[str, object]] = field(default_factory=list)
    listening_tab: object | None = None

    def require_browser(self) -> object:
        if self.browser is None:
            raise RuntimeError("Browser is not connected. Call browser_start_or_connect first.")
        return self.browser

    def tab(self) -> object:
        if self.active_tab is not None:
            return self.active_tab
        browser = self.require_browser()
        self.active_tab = browser.latest_tab
        return self.active_tab

    def locate(self, target: str, by: str = "auto", timeout: float = 5.0) -> object:
        tab = self.tab()
        locator = self.locator(target, by)
        element = tab.ele(locator, timeout=timeout)
        if not element:
            raise RuntimeError(f"Element not found: {locator}")
        return element

    def locator(self, target: str, by: str = "auto") -> str:
        normalized = by.lower()
        if normalized == "ref" or (normalized == "auto" and target in self.refs):
            ref = self.refs.get(target)
            if ref is None:
                raise RuntimeError(f"Unknown ref: {target}. Call page_snapshot again.")
            xpath = ref.get("xpath", "")
            if xpath:
                return f"xpath:{xpath}"
            css = ref.get("css", "")
            if css:
                return f"css:{css}"
            raise RuntimeError(f"Ref {target} has no usable selector.")
        if normalized == "css":
            return f"css:{target}"
        if normalized == "xpath":
            return f"xpath:{target}"
        if normalized == "text":
            return f"xpath://*[contains(normalize-space(.), {_xpath_literal(target)})]"
        if normalized == "role":
            return f"xpath://*[@role={_xpath_literal(target)}]"
        if target.startswith(("css:", "xpath:")):
            return target
        if target.startswith(("/", "(")):
            return f"xpath:{target}"
        return f"css:{target}"


state = BrowserState()


def _snapshot_script(
    max_elements: int,
    text_limit: int,
    include_hidden: bool,
    include_html: bool,
) -> str:
    return f"""
const maxElements = {max_elements};
const textLimit = {text_limit};
const includeHidden = {str(include_hidden).lower()};
const includeHtml = {str(include_html).lower()};

function cleanText(value) {{
  return (value || "").replace(/\\s+/g, " ").trim();
}}

function cssPath(el) {{
  if (!(el instanceof Element)) return "";
  if (el.id) return "#" + CSS.escape(el.id);
  const parts = [];
  while (el && el.nodeType === Node.ELEMENT_NODE && el !== document.body) {{
    let part = el.nodeName.toLowerCase();
    if (el.classList && el.classList.length) {{
      part += "." + Array.from(el.classList).slice(0, 3).map(CSS.escape).join(".");
    }}
    const parent = el.parentElement;
    if (parent) {{
      const siblings = Array.from(parent.children).filter((child) => child.nodeName === el.nodeName);
      if (siblings.length > 1) part += `:nth-of-type(${{siblings.indexOf(el) + 1}})`;
    }}
    parts.unshift(part);
    el = parent;
  }}
  return parts.length ? "body > " + parts.join(" > ") : "body";
}}

function xpath(el) {{
  if (!(el instanceof Element)) return "";
  if (el.id) return `//*[@id=${{JSON.stringify(el.id)}}]`;
  const parts = [];
  while (el && el.nodeType === Node.ELEMENT_NODE) {{
    let index = 1;
    let sibling = el.previousElementSibling;
    while (sibling) {{
      if (sibling.nodeName === el.nodeName) index++;
      sibling = sibling.previousElementSibling;
    }}
    parts.unshift(`${{el.nodeName.toLowerCase()}}[${{index}}]`);
    el = el.parentElement;
  }}
  return "/" + parts.join("/");
}}

function visible(el) {{
  if (!(el instanceof Element)) return false;
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0 &&
    (rect.width > 0 || rect.height > 0);
}}

function elementRole(el) {{
  return el.getAttribute("role") || "";
}}

function isInteractive(el) {{
  const tag = el.tagName.toLowerCase();
  return ["a", "button", "input", "select", "textarea", "summary", "option"].includes(tag) ||
    el.hasAttribute("onclick") || el.hasAttribute("contenteditable") || el.hasAttribute("role") ||
    el.tabIndex >= 0;
}}

const nodes = [];
const all = Array.from(document.querySelectorAll("body *"));
for (const el of all) {{
  if (!includeHidden && !visible(el)) continue;
  const text = cleanText(el.innerText || el.textContent || "");
  const aria = el.getAttribute("aria-label") || "";
  const title = el.getAttribute("title") || "";
  const placeholder = el.getAttribute("placeholder") || "";
  const value = el.value || "";
  const usefulText = cleanText([aria, title, placeholder, value, text].filter(Boolean).join(" | "));
  if (!isInteractive(el) && !usefulText) continue;
  const rect = el.getBoundingClientRect();
  nodes.push({{
    ref: `e${{nodes.length + 1}}`,
    tag: el.tagName.toLowerCase(),
    role: elementRole(el),
    type: el.getAttribute("type") || "",
    text: usefulText.slice(0, textLimit),
    href: el.getAttribute("href") || "",
    name: el.getAttribute("name") || "",
    id: el.id || "",
    class: el.className && typeof el.className === "string" ? el.className.slice(0, 120) : "",
    visible: visible(el),
    enabled: !el.disabled,
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    css: cssPath(el),
    xpath: xpath(el),
    html: includeHtml ? el.outerHTML.slice(0, 1000) : undefined
  }});
  if (nodes.length >= maxElements) break;
}}

return {{
  url: location.href,
  title: document.title,
  viewport: {{ width: innerWidth, height: innerHeight }},
  documentText: cleanText(document.body ? document.body.innerText : "").slice(0, Math.max(textLimit * 10, 4000)),
  elements: nodes,
  elementCount: nodes.length,
  truncated: all.length > nodes.length
}};
"""


def _store_snapshot(source: str, payload: object) -> dict[str, object]:
    snapshot_id = _now_id("snap")
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    state.snapshots[snapshot_id] = SnapshotRecord(payload=text, created_at=time.time(), source=source)
    return {"snapshot_id": snapshot_id, "chars": len(text)}


def _current_tab_info() -> dict[str, object]:
    tab = state.tab()
    return {
        "url": tab.url,
        "title": tab.title,
        "tab_id": tab.tab_id,
    }


def create_app() -> FastMCP:
    app = FastMCP("drissionpage-mcp", instructions=INSTRUCTIONS)

    @app.tool()
    def browser_start_or_connect(
        port: int = 9222,
        headless: bool = False,
        browser_path: str = "",
        user_data_dir: str = "",
        arguments: list[str] | None = None,
    ) -> dict[str, object]:
        """Start or attach to Chromium through DrissionPage."""
        from DrissionPage import Chromium, ChromiumOptions

        options = ChromiumOptions()
        options.set_local_port(port)
        if browser_path:
            options.set_browser_path(browser_path)
        if user_data_dir:
            options.set_user_data_path(user_data_dir)
        if headless:
            options.headless(True)
        for argument in arguments or []:
            options.set_argument(argument)

        try:
            state.browser = Chromium(options)
        except TypeError:
            state.browser = Chromium(addr_or_opts=options)
        state.active_tab = state.browser.latest_tab
        return {
            "status": "connected",
            "address": state.browser._chromium_options.address,
            "active_tab": _current_tab_info(),
            "next": "Call page_navigate or page_snapshot.",
        }

    @app.tool()
    def browser_close() -> dict[str, object]:
        """Close the controlled browser."""
        browser = state.require_browser()
        browser.quit()
        state.browser = None
        state.active_tab = None
        state.refs.clear()
        return {"status": "closed"}

    @app.tool()
    def tab_new(url: str = "about:blank", activate: bool = True) -> dict[str, object]:
        """Open a new browser tab."""
        browser = state.require_browser()
        tab = browser.new_tab(url)
        if activate:
            state.active_tab = tab
        return {"status": "opened", "tab": _current_tab_info()}

    @app.tool()
    def tab_list() -> dict[str, object]:
        """List browser tabs."""
        browser = state.require_browser()
        tabs = []
        for tab in browser.tabs:
            tabs.append({"tab_id": tab.tab_id, "title": tab.title, "url": tab.url})
        return {"active": _current_tab_info(), "tabs": tabs}

    @app.tool()
    def tab_activate(tab_id: str) -> dict[str, object]:
        """Activate a tab by DrissionPage tab id."""
        browser = state.require_browser()
        for tab in browser.tabs:
            if str(tab.tab_id) == str(tab_id):
                state.active_tab = tab
                return {"status": "activated", "tab": _current_tab_info()}
        raise RuntimeError(f"Tab not found: {tab_id}")

    @app.tool()
    def tab_close(tab_id: str = "") -> dict[str, object]:
        """Close a tab. Defaults to active tab."""
        browser = state.require_browser()
        target = state.tab()
        if tab_id:
            for tab in browser.tabs:
                if str(tab.tab_id) == str(tab_id):
                    target = tab
                    break
        target.close()
        state.active_tab = browser.latest_tab if browser.tabs else None
        return {"status": "closed", "active": _current_tab_info() if state.active_tab else None}

    @app.tool()
    def page_navigate(url: str, wait_seconds: float = 0.5) -> dict[str, object]:
        """Navigate the active tab to a URL."""
        if not state.browser:
            browser_start_or_connect()
        tab = state.tab()
        tab.get(url)
        if wait_seconds > 0:
            tab.wait(wait_seconds)
        return {"status": "navigated", "tab": _current_tab_info(), "next": "Call page_snapshot."}

    @app.tool()
    def page_back(wait_seconds: float = 0.5) -> dict[str, object]:
        """Go back in browser history."""
        tab = state.tab()
        tab.back()
        if wait_seconds > 0:
            tab.wait(wait_seconds)
        return {"status": "ok", "tab": _current_tab_info()}

    @app.tool()
    def page_forward(wait_seconds: float = 0.5) -> dict[str, object]:
        """Go forward in browser history."""
        tab = state.tab()
        tab.forward()
        if wait_seconds > 0:
            tab.wait(wait_seconds)
        return {"status": "ok", "tab": _current_tab_info()}

    @app.tool()
    def page_refresh(wait_seconds: float = 0.5) -> dict[str, object]:
        """Refresh the active tab."""
        tab = state.tab()
        tab.refresh()
        if wait_seconds > 0:
            tab.wait(wait_seconds)
        return {"status": "ok", "tab": _current_tab_info()}

    @app.tool()
    def page_info() -> dict[str, object]:
        """Return active page metadata."""
        return _current_tab_info()

    @app.tool()
    def wait(seconds: float = 1.0) -> dict[str, object]:
        """Wait using DrissionPage's tab wait."""
        tab = state.tab()
        tab.wait(seconds)
        return {"status": "waited", "seconds": seconds}

    @app.tool()
    def page_snapshot(
        max_elements: int = 120,
        max_chars: int = 12000,
        text_limit: int = 220,
        include_hidden: bool = False,
        include_html: bool = False,
        include_accessibility: bool = True,
    ) -> dict[str, object]:
        """Return a compact, AI-friendly snapshot and store the full payload for pagination."""
        tab = state.tab()
        dom = tab.run_js(_snapshot_script(max_elements, text_limit, include_hidden, include_html))
        if not isinstance(dom, dict):
            dom = {"raw": dom}

        refs: dict[str, dict[str, str]] = {}
        elements = dom.get("elements", [])
        if isinstance(elements, list):
            for item in elements:
                if isinstance(item, dict):
                    ref = str(item.get("ref", ""))
                    css = str(item.get("css", ""))
                    xpath = str(item.get("xpath", ""))
                    if ref:
                        refs[ref] = {"css": css, "xpath": xpath}
        state.refs = refs

        payload: dict[str, object] = {"dom": dom}
        if include_accessibility:
            try:
                tab.run_cdp("Accessibility.enable")
                payload["accessibility"] = tab.run_cdp("Accessibility.getFullAXTree")
            except Exception as exc:
                payload["accessibility_error"] = str(exc)

        stored = _store_snapshot("page_snapshot", payload)
        compact = _json(payload, max_chars=max_chars)
        return {
            **stored,
            "active_tab": _current_tab_info(),
            "ref_count": len(state.refs),
            "content": compact,
            "truncated": len(compact) < stored["chars"],
            "next": "Use element_* tools with refs like e1, or call snapshot_read for more.",
        }

    @app.tool()
    def snapshot_read(snapshot_id: str, offset: int = 0, limit: int = 12000) -> dict[str, object]:
        """Read a stored snapshot in chunks so no page information is lost."""
        record = state.snapshots.get(snapshot_id)
        if record is None:
            raise RuntimeError(f"Snapshot not found: {snapshot_id}")
        end = min(offset + limit, len(record.payload))
        return {
            "snapshot_id": snapshot_id,
            "source": record.source,
            "offset": offset,
            "end": end,
            "total_chars": len(record.payload),
            "content": record.payload[offset:end],
            "has_more": end < len(record.payload),
        }

    @app.tool()
    def page_text(selector: str = "body", by: Literal["css", "xpath", "auto"] = "css") -> dict[str, object]:
        """Get readable text from a page region."""
        element = state.locate(selector, by)
        return {"selector": selector, "text": element.text}

    @app.tool()
    def element_find(
        query: str,
        by: Literal["css", "xpath", "text", "role", "auto"] = "auto",
        timeout: float = 3.0,
        max_results: int = 20,
    ) -> dict[str, object]:
        """Find elements and return text, selector hints, and generated refs."""
        tab = state.tab()
        locator = state.locator(query, by)
        elements = tab.eles(locator, timeout=timeout)
        found = []
        for index, element in enumerate(elements[:max_results], start=1):
            ref = f"f{index}"
            xpath = element.run_js(
                """
function xp(el){if(el.id)return `//*[@id="${el.id}"]`;const p=[];while(el&&el.nodeType===1){let i=1,s=el.previousElementSibling;while(s){if(s.nodeName===el.nodeName)i++;s=s.previousElementSibling;}p.unshift(`${el.nodeName.toLowerCase()}[${i}]`);el=el.parentElement;}return "/" + p.join("/");}
return xp(this);
"""
            )
            state.refs[ref] = {"css": "", "xpath": str(xpath)}
            found.append(
                {
                    "ref": ref,
                    "tag": element.tag,
                    "text": _clean_selector_text(element.text)[:500],
                    "xpath": xpath,
                }
            )
        return {"query": query, "locator": locator, "count": len(elements), "results": found}

    @app.tool()
    def element_click(
        target: str,
        by: Literal["ref", "css", "xpath", "text", "role", "auto"] = "auto",
        timeout: float = 5.0,
        by_js: bool = False,
    ) -> dict[str, object]:
        """Click an element by snapshot ref, CSS, XPath, text, or role."""
        element = state.locate(target, by, timeout)
        element.click(by_js=by_js)
        return {"status": "clicked", "target": target, "tab": _current_tab_info()}

    @app.tool()
    def element_type(
        target: str,
        text: str,
        by: Literal["ref", "css", "xpath", "text", "role", "auto"] = "auto",
        timeout: float = 5.0,
        clear: bool = True,
    ) -> dict[str, object]:
        """Type text into an input-like element."""
        element = state.locate(target, by, timeout)
        element.input(text, clear=clear)
        return {"status": "typed", "target": target, "chars": len(text)}

    @app.tool()
    def element_select(
        target: str,
        value: str,
        by: Literal["ref", "css", "xpath", "auto"] = "auto",
        timeout: float = 5.0,
    ) -> dict[str, object]:
        """Select an option in a select element by visible text or value using JavaScript."""
        element = state.locate(target, by, timeout)
        result = element.run_js(
            """
const value = arguments[0];
const options = Array.from(this.options || []);
const option = options.find((item) => item.value === value || item.text.trim() === value);
if (!option) return {selected: false, available: options.map((item) => ({value: item.value, text: item.text}))};
this.value = option.value;
this.dispatchEvent(new Event("input", {bubbles: true}));
this.dispatchEvent(new Event("change", {bubbles: true}));
return {selected: true, value: option.value, text: option.text};
""",
            value,
        )
        return {"status": "selected", "target": target, "result": result}

    @app.tool()
    def element_hover(
        target: str,
        by: Literal["ref", "css", "xpath", "text", "role", "auto"] = "auto",
        timeout: float = 5.0,
    ) -> dict[str, object]:
        """Move the mouse over an element."""
        element = state.locate(target, by, timeout)
        element.hover()
        return {"status": "hovered", "target": target}

    @app.tool()
    def element_drag(
        target: str,
        offset_x: int,
        offset_y: int,
        by: Literal["ref", "css", "xpath", "auto"] = "auto",
        timeout: float = 5.0,
    ) -> dict[str, object]:
        """Drag an element by pixel offset."""
        tab = state.tab()
        element = state.locate(target, by, timeout)
        tab.actions.move_to(element).wait(0.2).hold().move(offset_x, offset_y).release()
        return {"status": "dragged", "target": target, "offset_x": offset_x, "offset_y": offset_y}

    @app.tool()
    def mouse_click_xy(x: int, y: int) -> dict[str, object]:
        """Click page coordinates."""
        tab = state.tab()
        tab.actions.click((x, y))
        return {"status": "clicked", "x": x, "y": y}

    @app.tool()
    def keyboard_press(key: str) -> dict[str, object]:
        """Send a special key or shortcut such as Enter, Ctrl+A, Ctrl+C."""
        from DrissionPage.common import Keys

        tab = state.tab()
        normalized = key.strip().lower()
        attr_name = SAFE_KEY_NAMES.get(normalized)
        if attr_name is None:
            tab.actions.type(key)
        else:
            key_value = Keys.__dict__[attr_name]
            tab.actions.type(key_value)
        return {"status": "pressed", "key": key}

    @app.tool()
    def js_eval(script: str, target: str = "", by: Literal["ref", "css", "xpath", "auto"] = "auto") -> dict[str, object]:
        """Run JavaScript on the page or on a selected element. Use return to provide a result."""
        result = state.locate(target, by).run_js(script) if target else state.tab().run_js(script)
        return {"result": result}

    @app.tool()
    def cdp_send(command: str, params_json: str = "{}") -> dict[str, object]:
        """Run a Chrome DevTools Protocol command against the active tab."""
        params = json.loads(params_json)
        if not isinstance(params, dict):
            raise RuntimeError("params_json must decode to an object")
        result = state.tab().run_cdp(command, **params)
        return {"command": command, "result": result}

    @app.tool()
    def network_start(
        url_contains: str = "",
        mime_contains: str = "",
        clear_existing: bool = True,
    ) -> dict[str, object]:
        """Start collecting Network.responseReceived events with optional filters."""
        tab = state.tab()
        if clear_existing:
            state.network_events.clear()
        tab.run_cdp("Network.enable")

        def callback(**event: object) -> None:
            response = event.get("response", {})
            if not isinstance(response, dict):
                return
            url = str(response.get("url", ""))
            mime = str(response.get("mimeType", ""))
            if url_contains and url_contains not in url:
                return
            if mime_contains and mime_contains not in mime:
                return
            state.network_events.append(
                {
                    "url": url,
                    "mimeType": mime,
                    "status": response.get("status"),
                    "method": response.get("requestHeaders", {}).get(":method", ""),
                    "timestamp": event.get("timestamp"),
                }
            )

        tab.driver.set_callback("Network.responseReceived", callback)
        state.listening_tab = tab
        return {"status": "listening", "url_contains": url_contains, "mime_contains": mime_contains}

    @app.tool()
    def network_stop(clear: bool = False) -> dict[str, object]:
        """Stop network collection."""
        tab = state.listening_tab or state.tab()
        tab.run_cdp("Network.disable")
        count = len(state.network_events)
        if clear:
            state.network_events.clear()
        return {"status": "stopped", "events": count, "cleared": clear}

    @app.tool()
    def network_logs(offset: int = 0, limit: int = 50) -> dict[str, object]:
        """Read collected network response events."""
        end = min(offset + limit, len(state.network_events))
        return {
            "offset": offset,
            "end": end,
            "total": len(state.network_events),
            "events": state.network_events[offset:end],
            "has_more": end < len(state.network_events),
        }

    @app.tool()
    def page_screenshot(
        path: str = "",
        name: str = "",
        full_page: bool = False,
        as_base64: bool = False,
    ) -> dict[str, object]:
        """Capture a page screenshot as a file path or base64 string."""
        tab = state.tab()
        if as_base64:
            image = tab.get_screenshot(full_page=full_page, as_bytes="png")
            return {"mime_type": "image/png", "base64": base64.b64encode(image).decode("ascii")}
        screenshot_path = tab.get_screenshot(path=path or None, name=name or None, full_page=full_page)
        return {"path": str(screenshot_path)}

    @app.tool()
    def download_file(url: str, save_path: str = ".", rename: str = "") -> dict[str, object]:
        """Download a file through the active tab."""
        path = Path(save_path).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        result = state.tab().download(file_url=url, save_path=str(path), rename=rename or None)
        return {"status": "downloaded", "result": str(result)}

    @app.tool()
    def upload_file(file_path: str, target: str = "//input[@type='file']", by: Literal["xpath", "css", "ref", "auto"] = "xpath") -> dict[str, object]:
        """Upload a local file through an input[type=file] element."""
        path = str(Path(file_path).expanduser())
        if not os.path.exists(path):
            raise RuntimeError(f"File not found: {path}")
        tab = state.tab()
        element = state.locate(target, by)
        tab.set.upload_files(path)
        element.click(by_js=True)
        tab.wait.upload_paths_inputted()
        return {"status": "uploaded", "file_path": path, "target": target}

    @app.tool()
    def cookies_get(all_domains: bool = True) -> dict[str, object]:
        """Return browser cookies."""
        browser = state.require_browser()
        if all_domains:
            return {"cookies": browser.cookies()}
        return {"cookies": state.tab().cookies()}

    @app.tool()
    def session_info() -> dict[str, object]:
        """Return MCP/browser state diagnostics."""
        return {
            "browser_connected": state.browser is not None,
            "active_tab": _current_tab_info() if state.browser is not None else None,
            "refs": len(state.refs),
            "snapshots": len(state.snapshots),
            "network_events": len(state.network_events),
        }

    return app


def main() -> None:
    create_app().run(transport="stdio")


if __name__ == "__main__":
    main()
