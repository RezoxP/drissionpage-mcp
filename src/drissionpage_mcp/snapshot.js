function cleanText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function cssPath(el) {
  if (!el || el.nodeType !== 1) return "";
  if (el.id) return "#" + CSS.escape(el.id);
  const parts = [];
  while (el && el.nodeType === 1 && el !== el.ownerDocument.body) {
    let part = el.nodeName.toLowerCase();
    if (el.classList && el.classList.length) {
      part += "." + Array.from(el.classList).slice(0, 3).map(CSS.escape).join(".");
    }
    const parent = el.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter((child) => child.nodeName === el.nodeName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(el) + 1})`;
    }
    parts.unshift(part);
    el = parent;
  }
  return parts.length ? "body > " + parts.join(" > ") : "body";
}

function xpath(el) {
  if (!el || el.nodeType !== 1) return "";
  if (el.id) return `//*[@id=${JSON.stringify(el.id)}]`;
  const parts = [];
  while (el && el.nodeType === 1) {
    let index = 1;
    let sibling = el.previousElementSibling;
    while (sibling) {
      if (sibling.nodeName === el.nodeName) index++;
      sibling = sibling.previousElementSibling;
    }
    parts.unshift(`${el.nodeName.toLowerCase()}[${index}]`);
    el = el.parentElement;
  }
  return "/" + parts.join("/");
}

function visible(el) {
  if (!el || el.nodeType !== 1) return false;
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0 &&
    (rect.width > 0 || rect.height > 0);
}

function elementRole(el) {
  const explicit = el.getAttribute("role") || "";
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  if (tag === "nav") return "navigation";
  if (tag === "main") return "main";
  if (tag === "header") return "banner";
  if (tag === "footer") return "contentinfo";
  if (tag === "aside") return "complementary";
  if (tag === "form") return "form";
  if (tag === "button") return "button";
  if (tag === "a" && el.hasAttribute("href")) return "link";
  if (["input", "select", "textarea"].includes(tag)) return tag === "select" ? "combobox" : "textbox";
  return "";
}

function isInteractive(el) {
  const tag = el.tagName.toLowerCase();
  return ["a", "button", "input", "select", "textarea", "summary", "option", "iframe", "frame"].includes(tag) ||
    el.hasAttribute("onclick") || el.hasAttribute("contenteditable") || el.hasAttribute("role") ||
    el.tabIndex >= 0;
}

const nodes = [];
let scannedCount = 0;
let frameIndex = 0;
function stableHash(input) {
  let h = 2166136261;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
function nearestLandmark(el) {
  const landmark = el.closest("main, nav, header, footer, aside, form, section, [role]");
  if (!landmark) return { role: "document", label: "document" };
  const role = elementRole(landmark) || landmark.tagName.toLowerCase();
  const label = cleanText(landmark.getAttribute("aria-label") || landmark.getAttribute("title") || landmark.id || role);
  return { role, label };
}
function collect(rootDocument, frameMeta = null) {
const all = Array.from(rootDocument.querySelectorAll("body *"));
scannedCount += all.length;
for (const el of all) {
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
  if (tag === "iframe" || tag === "frame") {
    frameUrl = el.src || "";
    try {
      frameTitle = el.contentDocument ? el.contentDocument.title : "";
    } catch (error) {}
  }
  const usefulText = cleanText([aria, title, placeholder, value, frameTitle, frameUrl, text].filter(Boolean).join(" | "));
  const role = elementRole(el);
  if (tagFilter.size && !tagFilter.has(tag)) continue;
  if (roleFilter.size && !roleFilter.has(role.toLowerCase())) continue;
  if (!isInteractive(el) && !usefulText) continue;
  const rect = el.getBoundingClientRect();
  const localCss = cssPath(el);
  const localXpath = xpath(el);
  const framePrefix = frameMeta ? frameMeta.ref + "." : "";
  const textFingerprint = usefulText.slice(0, 80);
  const stableKey = stableHash([framePrefix, localXpath, tag, role, textFingerprint].join("|"));
  const landmark = nearestLandmark(el);
  const inputState = {};
  if (["input", "textarea", "select", "option"].includes(tag)) {
    inputState.value = el.value || "";
    inputState.checked = Boolean(el.checked);
    inputState.selected = Boolean(el.selected);
  }
  nodes.push({
    ref: `${framePrefix}e${nodes.length + 1}`,
    stableKey,
    tag: el.tagName.toLowerCase(),
    role,
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
    landmark,
    value: inputState.value,
    checked: inputState.checked,
    selected: inputState.selected,
    html: includeHtml ? el.outerHTML.slice(0, 1000) : undefined
  });
}
}

collect(document);
const frames = Array.from(document.querySelectorAll("iframe, frame"));
for (const frame of frames) {
  if (nodes.length >= maxElements) break;
  try {
    if (frame.contentDocument) {
      frameIndex++;
      collect(frame.contentDocument, { ref: `f${frameIndex}`, css: cssPath(frame), xpath: xpath(frame), url: frame.src || "" });
    }
  } catch (error) {}
}

const landmarkMap = {};
for (const node of nodes) {
  const key = `${node.landmark.role}:${node.landmark.label}`;
  if (!landmarkMap[key]) landmarkMap[key] = { role: node.landmark.role, label: node.landmark.label, elements: [] };
  landmarkMap[key].elements.push({
    ref: node.ref,
    tag: node.tag,
    role: node.role,
    text: node.text,
    href: node.href,
    value: node.value,
    checked: node.checked,
    selected: node.selected
  });
}

return {
  url: location.href,
  title: document.title,
  viewport: { width: innerWidth, height: innerHeight },
  documentText: cleanText(document.body ? document.body.innerText : "").slice(0, Math.max(textLimit * 10, 4000)),
  elements: nodes,
  landmarks: Object.values(landmarkMap),
  elementCount: nodes.length,
  truncated: scannedCount > nodes.length
};
