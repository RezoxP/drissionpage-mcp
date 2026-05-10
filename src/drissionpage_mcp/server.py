from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
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

DEFAULT_BROWSER_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "msedge",
    "brave-browser",
    "brave",
)

GIT_PACKAGE_URL = "git+https://github.com/RezoxP/drissionpage-mcp"


def _now_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars] + f"\n… truncated {len(value) - max_chars} chars"


def _json(value: object, max_chars: int = 12000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return _truncate(text, max_chars)


def _ok(**values: object) -> dict[str, object]:
    return {"ok": True, **values}


def _error(tool: str, exc: Exception, next_step: str) -> dict[str, object]:
    return {
        "ok": False,
        "tool": tool,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "next": next_step,
    }


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


def _prefixed_locator(css: str = "", xpath: str = "") -> str:
    if xpath:
        return f"xpath:{xpath}"
    if css:
        return f"css:{css}"
    return ""


def _first_existing_path(paths: Sequence[str]) -> str:
    for raw_path in paths:
        path = _normalize_browser_path(raw_path)
        if path and os.path.exists(path):
            return path
    return ""


def _normalize_browser_path(path: str) -> str:
    normalized = path.strip().strip('"').strip("'")
    return os.path.expandvars(os.path.expanduser(normalized))


def _find_browser_binary(explicit_path: str = "") -> str:
    if explicit_path:
        resolved = _first_existing_path([explicit_path])
        if not resolved:
            raise RuntimeError(f"Browser binary does not exist: {explicit_path}")
        return resolved

    env_path = os.getenv("DRISSIONPAGE_MCP_BROWSER_BINARY") or os.getenv("CHROME_PATH")
    if env_path:
        resolved = _first_existing_path([env_path])
        if not resolved:
            raise RuntimeError(f"Configured browser binary does not exist: {env_path}")
        return resolved

    common_paths = (
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    resolved = _first_existing_path(common_paths)
    if resolved:
        return resolved

    for candidate in DEFAULT_BROWSER_CANDIDATES:
        resolved_command = shutil.which(candidate)
        if resolved_command:
            return resolved_command

    return ""


def _package_version() -> str:
    try:
        return importlib.metadata.version("drissionpage-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "editable/local"


def _mcp_config(command: str, args: Sequence[str]) -> dict[str, object]:
    return {
        "mcpServers": {
            "drissionpage": {
                "command": command,
                "args": list(args),
            }
        }
    }


def _recommended_configs(browser_binary: str = "", install_from_git: bool = True) -> dict[str, object]:
    binary_args = ["--browser-binary", browser_binary] if browser_binary else []
    if install_from_git:
        return _mcp_config("uvx", ["--from", GIT_PACKAGE_URL, "drissionpage-mcp", *binary_args])
    return _mcp_config("drissionpage-mcp", binary_args)


def _doctor_report(browser_binary: str = "", install_from_git: bool = True) -> dict[str, object]:
    normalized_binary = _normalize_browser_path(browser_binary) if browser_binary else ""
    binary_exists = bool(normalized_binary and os.path.exists(normalized_binary))
    resolved_browser = ""
    browser_error = ""
    try:
        resolved_browser = _find_browser_binary(normalized_binary)
    except RuntimeError as exc:
        browser_error = str(exc)

    command = "uvx" if install_from_git else "drissionpage-mcp"
    command_found = shutil.which(command) is not None
    return {
        "package_version": _package_version(),
        "python": sys.version.split()[0],
        "command": command,
        "command_found": command_found,
        "browser_binary_input": browser_binary,
        "browser_binary_normalized": normalized_binary,
        "browser_binary_exists": binary_exists,
        "resolved_browser": resolved_browser,
        "browser_error": browser_error,
        "mcp_config": _recommended_configs(normalized_binary, install_from_git),
        "tips": [
            "Use the JSON config instead of typing the whole uv command as one MCP command.",
            "On Windows, keep the browser path as one JSON string in args.",
            "If using a local checkout, run uv sync first and set command to uv with args ['run', 'drissionpage-mcp', ...].",
        ],
    }


@dataclass
class SnapshotRecord:
    payload: str
    created_at: float
    source: str


@dataclass
class BrowserState:
    browser: object | None = None
    active_tab: object | None = None
    known_tabs: dict[str, object] = field(default_factory=dict)
    refs: dict[str, dict[str, object]] = field(default_factory=dict)
    snapshots: dict[str, SnapshotRecord] = field(default_factory=dict)
    network_events: list[dict[str, object]] = field(default_factory=list)
    listening_tab: object | None = None

    def require_browser(self) -> object:
        if self.browser is None:
            raise RuntimeError("Browser is not connected. Call browser_start_or_connect first.")
        return self.browser

    def tab(self) -> object:
        if self.active_tab is not None:
            self.remember_tab(self.active_tab)
            return self.active_tab
        browser = self.require_browser()
        self.active_tab = browser.latest_tab
        self.remember_tab(self.active_tab)
        return self.active_tab

    def remember_tab(self, tab: object | None) -> None:
        if tab is None:
            return
        tab_id = str(getattr(tab, "tab_id", ""))
        if tab_id:
            self.known_tabs[tab_id] = tab

    def get_tabs(self) -> list[object]:
        browser = self.require_browser()
        tabs: list[object] = []
        if hasattr(browser, "get_tabs"):
            try:
                tabs = list(browser.get_tabs())
            except Exception:
                tabs = []
        if not tabs:
            tabs = list(self.known_tabs.values())
        latest = getattr(browser, "latest_tab", None)
        if latest is not None:
            tabs.append(latest)
        unique: dict[str, object] = {}
        for tab in tabs:
            tab_id = str(getattr(tab, "tab_id", ""))
            if tab_id:
                unique[tab_id] = tab
        self.known_tabs.update(unique)
        return list(unique.values())

    def activate_tab(self, tab_id: str) -> object:
        browser = self.require_browser()
        for tab in self.get_tabs():
            if str(getattr(tab, "tab_id", "")) == str(tab_id):
                if hasattr(browser, "activate_tab"):
                    browser.activate_tab(tab)
                self.active_tab = tab
                self.remember_tab(tab)
                return tab
        raise RuntimeError(f"Tab not found: {tab_id}. Call tab_list to see available tabs.")

    def close_tab(self, tab_id: str = "") -> object | None:
        browser = self.require_browser()
        target = self.tab()
        if tab_id:
            target = self.activate_tab(tab_id)
        target_id = str(getattr(target, "tab_id", ""))
        if hasattr(browser, "close_tabs"):
            browser.close_tabs(target)
        else:
            target.close()
        self.known_tabs.pop(target_id, None)
        tabs = self.get_tabs()
        self.active_tab = tabs[-1] if tabs else None
        return self.active_tab

    def locate(self, target: str, by: str = "auto", timeout: float = 5.0) -> object:
        normalized = by.lower()
        if normalized == "ref" or (normalized == "auto" and target in self.refs):
            return self.locate_ref(target, timeout)
        tab = self.tab()
        locator = self.locator(target, by)
        element = tab.ele(locator, timeout=timeout)
        if not element:
            raise RuntimeError(f"Element not found: {locator}")
        return element

    def locate_ref(self, target: str, timeout: float = 5.0) -> object:
        tab = self.tab()
        ref = self.refs.get(target)
        if ref is None:
            raise RuntimeError(f"Unknown ref: {target}. Call page_snapshot or element_find again.")
        locator = _prefixed_locator(str(ref.get("css", "")), str(ref.get("xpath", "")))
        if not locator:
            raise RuntimeError(f"Ref {target} has no usable selector.")

        frame_locator = _prefixed_locator(
            str(ref.get("frame_css", "")),
            str(ref.get("frame_xpath", "")),
        )
        search_context = tab
        if frame_locator:
            search_context = _locate_frame(tab, frame_locator, timeout)

        element = search_context.ele(locator, timeout=timeout)
        if not element:
            raise RuntimeError(f"Element not found for ref {target}: {locator}")
        return element

    def locator(self, target: str, by: str = "auto") -> str:
        normalized = by.lower()
        if normalized == "ref" or (normalized == "auto" and target in self.refs):
            ref = self.refs.get(target)
            if ref is None:
                raise RuntimeError(f"Unknown ref: {target}. Call page_snapshot again.")
            xpath = str(ref.get("xpath", ""))
            if xpath:
                return f"xpath:{xpath}"
            css = str(ref.get("css", ""))
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
  if (!el || el.nodeType !== 1) return "";
  if (el.id) return "#" + CSS.escape(el.id);
  const parts = [];
  while (el && el.nodeType === 1 && el !== el.ownerDocument.body) {{
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
  if (!el || el.nodeType !== 1) return "";
  if (el.id) return `//*[@id=${{JSON.stringify(el.id)}}]`;
  const parts = [];
  while (el && el.nodeType === 1) {{
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
  if (!el || el.nodeType !== 1) return false;
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
  return ["a", "button", "input", "select", "textarea", "summary", "option", "iframe", "frame"].includes(tag) ||
    el.hasAttribute("onclick") || el.hasAttribute("contenteditable") || el.hasAttribute("role") ||
    el.tabIndex >= 0;
}}

const nodes = [];
let scannedCount = 0;
function collect(rootDocument, frameMeta = null) {{
const all = Array.from(rootDocument.querySelectorAll("body *"));
scannedCount += all.length;
for (const el of all) {{
  if (nodes.length >= maxElements) break;
  if (!includeHidden && !visible(el)) continue;
  const text = cleanText(el.innerText || el.textContent || "");
  const aria = el.getAttribute("aria-label") || "";
  const title = el.getAttribute("title") || "";
  const placeholder = el.getAttribute("placeholder") || "";
  const value = el.value || "";
  let frameUrl = "";
  let frameTitle = "";
  const tag = el.tagName.toLowerCase();
  if (tag === "iframe" || tag === "frame") {{
    frameUrl = el.src || "";
    try {{
      frameTitle = el.contentDocument ? el.contentDocument.title : "";
    }} catch (error) {{}}
  }}
  const usefulText = cleanText([aria, title, placeholder, value, frameTitle, frameUrl, text].filter(Boolean).join(" | "));
  if (!isInteractive(el) && !usefulText) continue;
  const rect = el.getBoundingClientRect();
  const localCss = cssPath(el);
  const localXpath = xpath(el);
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
    css: localCss,
    xpath: localXpath,
    frameCss: frameMeta ? frameMeta.css : "",
    frameXpath: frameMeta ? frameMeta.xpath : "",
    frameUrl: frameMeta ? frameMeta.url : frameUrl,
    html: includeHtml ? el.outerHTML.slice(0, 1000) : undefined
  }});
}}
}}

collect(document);
const frames = Array.from(document.querySelectorAll("iframe, frame"));
for (const frame of frames) {{
  if (nodes.length >= maxElements) break;
  try {{
    if (frame.contentDocument) {{
      collect(frame.contentDocument, {{ css: cssPath(frame), xpath: xpath(frame), url: frame.src || "" }});
    }}
  }} catch (error) {{}}
}}

return {{
  url: location.href,
  title: document.title,
  viewport: {{ width: innerWidth, height: innerHeight }},
  documentText: cleanText(document.body ? document.body.innerText : "").slice(0, Math.max(textLimit * 10, 4000)),
  elements: nodes,
  elementCount: nodes.length,
  truncated: scannedCount > nodes.length
}};
"""


def _store_snapshot(source: str, payload: object) -> dict[str, object]:
    snapshot_id = _now_id("snap")
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    state.snapshots[snapshot_id] = SnapshotRecord(payload=text, created_at=time.time(), source=source)
    return {"snapshot_id": snapshot_id, "chars": len(text)}


def _tab_info(tab: object) -> dict[str, object]:
    return {
        "url": getattr(tab, "url", ""),
        "title": getattr(tab, "title", ""),
        "tab_id": getattr(tab, "tab_id", ""),
    }


def _current_tab_info() -> dict[str, object]:
    return _tab_info(state.tab())


def _run_js_safely(tab: object, script: str, *args: object, as_expr: bool = False) -> object:
    try:
        if hasattr(tab, "run_js_loaded"):
            return tab.run_js_loaded(script, *args, as_expr=as_expr)
        return tab.run_js(script, *args, as_expr=as_expr)
    except Exception as first_exc:
        message = str(first_exc).lower()
        if "runtime" in message or "context" in message or "faulty" in message:
            try:
                tab.wait(0.5)
                return tab.run_js(script, *args, as_expr=as_expr)
            except Exception:
                pass
        raise first_exc


def _locate_frame(tab: object, frame_locator: str, timeout: float) -> object:
    frame = tab.ele(frame_locator, timeout=timeout)
    if not frame:
        raise RuntimeError(f"Frame not found: {frame_locator}")
    tag = str(getattr(frame, "tag", "")).lower()
    if tag in {"iframe", "frame"} and hasattr(frame, "ele"):
        return frame
    if hasattr(frame, "frame_ele"):
        frame_page = frame.frame_ele
        if frame_page is not None:
            return frame_page
    if hasattr(tab, "get_frame"):
        return tab.get_frame(frame_locator, timeout=timeout)
    raise RuntimeError(f"Located frame is not searchable: {frame_locator}")


def _candidate_locators(query: str, by: str) -> list[str]:
    normalized = by.lower()
    if normalized == "css":
        return [f"css:{query}"]
    if normalized == "xpath":
        return [f"xpath:{query}"]
    if normalized == "text":
        return [f"xpath://*[contains(normalize-space(.), {_xpath_literal(query)})]"]
    if normalized == "role":
        return [f"xpath://*[@role={_xpath_literal(query)}]"]
    if query.startswith(("css:", "xpath:")):
        return [query]
    if query.startswith(("/", "(")):
        return [f"xpath:{query}"]

    candidates = [f"css:{query}"]
    if re.fullmatch(r"[\w -]{1,80}", query):
        candidates.append(f"xpath://*[@role={_xpath_literal(query)}]")
    candidates.append(f"xpath://*[contains(normalize-space(.), {_xpath_literal(query)})]")

    deduped = []
    for locator in candidates:
        if locator not in deduped:
            deduped.append(locator)
    return deduped


def _js_expression(expression: str) -> str:
    stripped = expression.strip()
    if stripped.startswith("return ") or "\n" in stripped or ";" in stripped:
        return stripped
    return f"return ({stripped});"


def _element_xpath_fallback(tab: object, element: object, index: int) -> str:
    element_id = getattr(element, "attr", lambda _: "")("id") if hasattr(element, "attr") else ""
    if element_id:
        return f"//*[@id={_xpath_literal(str(element_id))}]"
    tag = getattr(element, "tag", "*") or "*"
    text = _element_text(element)
    if text:
        return f"(//{tag}[contains(normalize-space(.), {_xpath_literal(text[:80])})])[{index}]"
    return f"(//{tag})[{index}]"


def _element_text(element: object, limit: int = 500) -> str:
    if hasattr(element, "attr"):
        values = []
        for name in ("aria-label", "title", "placeholder", "value", "src", "href", "name", "id"):
            with contextlib.suppress(Exception):
                value = element.attr(name)
                if value:
                    values.append(str(value))
        if values:
            return _clean_selector_text(" | ".join(values))[:limit]
    for attr_name in ("text", "inner_html", "html"):
        value = getattr(element, attr_name, None)
        if callable(value):
            with contextlib.suppress(Exception):
                value = value()
        if value:
            return _clean_selector_text(str(value))[:limit]
    return ""


def _css_selector_for_element(element: object) -> str:
    element_id = ""
    if hasattr(element, "attr"):
        with contextlib.suppress(Exception):
            element_id = str(element.attr("id") or "")
    if element_id:
        return "#" + re.sub(r"([ #.:,[\]>+~*'\"\\])", r"\\\1", element_id)
    return ""


def create_app(log_level: str = "ERROR") -> FastMCP:
    app = FastMCP("drissionpage-mcp", instructions=INSTRUCTIONS, log_level=log_level)

    @app.tool()
    def browser_find_binary(browser_binary: str = "") -> dict[str, object]:
        """Resolve the browser executable that will be used for new browser sessions."""
        resolved = _find_browser_binary(browser_binary)
        return {
            "browser_binary": resolved,
            "found": bool(resolved),
            "env": {
                "DRISSIONPAGE_MCP_BROWSER_BINARY": bool(
                    os.getenv("DRISSIONPAGE_MCP_BROWSER_BINARY")
                ),
                "CHROME_PATH": bool(os.getenv("CHROME_PATH")),
            },
            "candidates": list(DEFAULT_BROWSER_CANDIDATES),
            "next": "Pass browser_binary to browser_start_or_connect if this is empty or wrong.",
        }

    @app.tool()
    def install_help(browser_binary: str = "", install_from_git: bool = True) -> dict[str, object]:
        """Return ready-to-copy MCP configuration and installation diagnostics."""
        return _doctor_report(browser_binary, install_from_git)

    @app.tool()
    def browser_start_or_connect(
        port: int = 9222,
        headless: bool = False,
        browser_binary: str = "",
        browser_path: str = "",
        user_data_dir: str = "",
        arguments: list[str] | None = None,
    ) -> dict[str, object]:
        """Start or attach to Chromium through DrissionPage.

        browser_binary can point to Chrome, Chromium, Edge, Brave, or another Chromium-compatible
        executable when Chrome is not installed. The legacy browser_path name is still accepted.
        """
        from DrissionPage import Chromium, ChromiumOptions

        options = ChromiumOptions()
        options.set_local_port(port)
        requested_binary = browser_binary or browser_path
        resolved_binary = _find_browser_binary(requested_binary)
        if resolved_binary:
            options.set_browser_path(resolved_binary)
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
        state.known_tabs.clear()
        state.remember_tab(state.active_tab)
        return {
            "status": "connected",
            "address": state.browser._chromium_options.address,
            "browser_binary": resolved_binary or "DrissionPage default",
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
        state.known_tabs.clear()
        state.refs.clear()
        return {"status": "closed"}

    @app.tool()
    def tab_new(url: str = "about:blank", activate: bool = True) -> dict[str, object]:
        """Open a new browser tab."""
        browser = state.require_browser()
        tab = browser.new_tab(url)
        state.remember_tab(tab)
        if activate:
            state.active_tab = tab
        return {"status": "opened", "tab": _tab_info(tab)}

    @app.tool()
    def tab_list() -> dict[str, object]:
        """List browser tabs."""
        state.require_browser()
        tabs = [_tab_info(tab) for tab in state.get_tabs()]
        return {"active": _current_tab_info(), "tabs": tabs, "count": len(tabs)}

    @app.tool()
    def tab_activate(tab_id: str) -> dict[str, object]:
        """Activate a tab by DrissionPage tab id."""
        try:
            tab = state.activate_tab(tab_id)
            return {"status": "activated", "tab": _tab_info(tab)}
        except Exception as exc:
            return _error("tab_activate", exc, "Call tab_list, then pass one of the returned tab_id values.")

    @app.tool()
    def tab_close(tab_id: str = "") -> dict[str, object]:
        """Close a tab. Defaults to active tab."""
        try:
            active = state.close_tab(tab_id)
            return {"status": "closed", "active": _tab_info(active) if active else None}
        except Exception as exc:
            return _error("tab_close", exc, "Call tab_list to refresh known tabs, then retry.")

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
        try:
            dom = _run_js_safely(
                tab,
                _snapshot_script(max_elements, text_limit, include_hidden, include_html),
            )
        except Exception as exc:
            text = ""
            with contextlib.suppress(Exception):
                text = tab("t:body").text
            payload = {
                "dom": {
                    "url": getattr(tab, "url", ""),
                    "title": getattr(tab, "title", ""),
                    "documentText": text,
                    "elements": [],
                },
                "snapshot_error": str(exc),
                "recovery": "JavaScript snapshot failed; returned body text fallback. Retry page_refresh then page_snapshot.",
            }
            stored = _store_snapshot("page_snapshot_fallback", payload)
            return {
                **stored,
                "ok": False,
                "active_tab": _current_tab_info(),
                "ref_count": 0,
                "content": _json(payload, max_chars=max_chars),
                "truncated": False,
                "next": "Call page_refresh, then page_snapshot. Use page_text if JS remains unavailable.",
            }
        if not isinstance(dom, dict):
            dom = {"raw": dom}

        refs: dict[str, dict[str, object]] = {}
        elements = dom.get("elements", [])
        if isinstance(elements, list):
            for item in elements:
                if isinstance(item, dict):
                    ref = str(item.get("ref", ""))
                    css = str(item.get("css", ""))
                    xpath = str(item.get("xpath", ""))
                    if ref:
                        refs[ref] = {
                            "css": css,
                            "xpath": xpath,
                            "frame_css": str(item.get("frameCss", "")),
                            "frame_xpath": str(item.get("frameXpath", "")),
                        }
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
        return {"selector": selector, "text": _element_text(element, limit=20000)}

    @app.tool()
    def element_find(
        query: str,
        by: Literal["css", "xpath", "text", "role", "auto"] = "auto",
        timeout: float = 3.0,
        max_results: int = 20,
        include_frames: bool = True,
    ) -> dict[str, object]:
        """Find elements quickly across the page and same-origin frames, then return generated refs."""
        tab = state.tab()
        locators = _candidate_locators(query, by)
        search_contexts: list[tuple[object, str, str, str]] = [(tab, "", "", "main")]
        if include_frames:
            with contextlib.suppress(Exception):
                frames = tab.eles("xpath://iframe|//frame", timeout=min(timeout, 0.5))
                for frame_index, frame in enumerate(frames[: max(max_results, 1)], start=1):
                    frame_xpath = _element_xpath_fallback(tab, frame, frame_index)
                    frame_css = _css_selector_for_element(frame)
                    search_contexts.append((frame, frame_css, frame_xpath, f"frame:{frame_index}"))

        found = []
        total_count = 0
        matched_locator = ""
        for search_context, frame_css, frame_xpath, frame_label in search_contexts:
            if len(found) >= max_results:
                break
            elements = []
            for locator in locators:
                try:
                    elements = search_context.eles(locator, timeout=timeout if not found else 0.2)
                except Exception:
                    continue
                if elements:
                    matched_locator = locator
                    break
            total_count += len(elements)
            for element in elements:
                if len(found) >= max_results:
                    break
                index = len(found) + 1
                ref = f"f{index}"
                xpath = ""
                with contextlib.suppress(Exception):
                    xpath = str(
                        _run_js_safely(
                            element,
                            """
function xp(el){if(el.id)return `//*[@id="${el.id}"]`;const p=[];while(el&&el.nodeType===1){let i=1,s=el.previousElementSibling;while(s){if(s.nodeName===el.nodeName)i++;s=s.previousElementSibling;}p.unshift(`${el.nodeName.toLowerCase()}[${i}]`);el=el.parentElement;}return "/" + p.join("/");}
return xp(this);
""",
                        )
                    )
                if not xpath:
                    xpath = _element_xpath_fallback(search_context, element, index)
                state.refs[ref] = {
                    "css": "",
                    "xpath": str(xpath),
                    "frame_css": frame_css,
                    "frame_xpath": frame_xpath,
                }
                found.append(
                    {
                        "ref": ref,
                        "tag": getattr(element, "tag", ""),
                        "text": _element_text(element),
                        "xpath": xpath,
                        "frame": frame_label,
                    }
                )
        return {
            "query": query,
            "locator": matched_locator or locators[0],
            "tried_locators": locators[:3],
            "count": total_count,
            "returned": len(found),
            "results": found,
        }

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
        by: Literal["ref", "css", "xpath", "text", "role", "auto"] = "auto",
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
    def js_eval(script: str, target: str = "", by: Literal["ref", "css", "xpath", "text", "role", "auto"] = "auto") -> dict[str, object]:
        """Run JavaScript on the page or on a selected element.

        Simple expressions like `document.title` are wrapped with `return (...)` automatically.
        Multi-line scripts or scripts starting with `return` are executed as-is.
        """
        try:
            prepared = _js_expression(script)
            runner = state.locate(target, by) if target else state.tab()
            result = _run_js_safely(runner, prepared)
            return _ok(result=result, returned_null=result is None)
        except Exception as exc:
            return _error("js_eval", exc, "Try page_refresh, then retry. For raw CDP, use cdp_send.")

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
        safe_url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
        try:
            result = state.tab().download(file_url=safe_url, save_path=str(path), rename=rename or None)
            return _ok(status="downloaded", result=str(result), url=safe_url)
        except UnicodeEncodeError:
            filename = rename or Path(urllib.parse.urlparse(url).path).name or "download"
            target = path / filename
            urllib.request.urlretrieve(safe_url, target)
            return _ok(status="downloaded", result=str(target), url=safe_url, fallback="urllib")
        except Exception as exc:
            return _error(
                "download_file",
                exc,
                "Check URL encoding and save_path. Unicode URLs are percent-encoded automatically.",
            )

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
            "known_tabs": len(state.known_tabs),
            "refs": len(state.refs),
            "snapshots": len(state.snapshots),
            "network_events": len(state.network_events),
            "ai_hints": [
                "Use page_snapshot before element actions so refs are fresh.",
                "If JavaScript tools report runtime faults, call page_refresh then retry.",
                "Use snapshot_read when content is truncated instead of asking for another full snapshot.",
            ],
        }

    return app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="DrissionPage MCP server")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Print install diagnostics and a ready-to-copy MCP config, then exit.",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print only a ready-to-copy MCP JSON config, then exit.",
    )
    parser.add_argument(
        "--local-config",
        action="store_true",
        help="Generate config for an already installed drissionpage-mcp command instead of uvx.",
    )
    parser.add_argument(
        "--browser-binary",
        default="",
        help=(
            "Path to a Chromium-compatible browser binary. Also configurable with "
            "DRISSIONPAGE_MCP_BROWSER_BINARY."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("DRISSIONPAGE_MCP_LOG_LEVEL", "ERROR"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="Logging level for the MCP server.",
    )
    args = parser.parse_args(argv)
    if args.browser_binary:
        os.environ["DRISSIONPAGE_MCP_BROWSER_BINARY"] = _normalize_browser_path(args.browser_binary)

    if args.doctor:
        print(
            json.dumps(
                _doctor_report(args.browser_binary, install_from_git=not args.local_config),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.print_config:
        print(
            json.dumps(
                _recommended_configs(
                    _normalize_browser_path(args.browser_binary),
                    install_from_git=not args.local_config,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    try:
        create_app(log_level=args.log_level).run(transport="stdio")
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"drissionpage-mcp startup failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
