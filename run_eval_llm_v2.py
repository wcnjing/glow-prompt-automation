"""
LLM-driven prompt evaluation, take 2.

Same goal as run_eval_llm.py but without Browser Use. The flow:

  1. Open the chatbot site in Playwright (same as run_eval.py).
  2. After login, ask Groq (Llama 3.3 70B) ONCE: "given this page HTML, what are
     the CSS selectors for the chat input, send button, and message container?"
  3. For each prompt: type into the input, send, wait for the message container
     to stabilize with new content, capture the new text, write to CSV.

That's it. No agent framework, no per-step LLM calls, no session lifecycle
games. The LLM's only job is to read DOM and emit selectors — Playwright does
the actual driving.

Setup:
    pip install playwright
    playwright install chromium
    export GROQ_API_KEY="your-key-from-console.groq.com/keys"

Run:
    python run_eval_llm_v2.py
"""

import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# On corporate networks (e.g. MOE) HTTPS traffic is often intercepted by a
# proxy that re-signs certs with a private CA. That CA is in the macOS
# Keychain but not in Python's bundled cert store. truststore makes Python
# use the system trust store so we see the corporate CA.
try:
    import truststore
    _SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:
    # Fallback: certifi's bundle. Works on home networks, may fail on
    # corporate networks with TLS inspection.
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# ---------------------------------------------------------------------------
# Config — change BASE_URL and prompts/results files to point at a new product.
# Everything below this is product-agnostic.
# ---------------------------------------------------------------------------

BASE_URL = "https://stg-glow.edutech.works/login"
PROMPTS_FILE = "prompts.txt"
RESULTS_FILE = "results_llm.csv"
USER_DATA_DIR = "browser_session"

GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Response waiting: snapshot text in messages_container before sending, then
# poll. Response is "done" when content has been unchanged for STABLE_SECONDS.
POLL_INTERVAL_SECONDS = 1.0
STABLE_SECONDS = 6.0
RESPONSE_MAX_WAIT_SECONDS = 240.0

# How long to wait for the user to log in / open the chat panel.
LOGIN_TIMEOUT_SECONDS = 300

CSV_FIELDS = ["id", "prompt", "response", "status"]


# ---------------------------------------------------------------------------
# CSV / prompts helpers (same shape as run_eval.py)
# ---------------------------------------------------------------------------

def load_prompts(path):
    prompts = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            cleaned = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", line)
            prompts.append(cleaned)
    return prompts


def load_done_ids(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("id"):
                done.add(str(row["id"]))
    return done


def append_result(path, row):
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Groq call (no SDK — just stdlib HTTP). Returns the assistant message string.
# ---------------------------------------------------------------------------

def groq_chat(messages, temperature=0.0, response_format=None):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set")

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format

    req = urllib.request.Request(
        GROQ_BASE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Cloudflare (in front of Groq) blocks the default Python-urllib
            # User-Agent as a bot signature. Send a normal one.
            "User-Agent": "glow-eval/1.0 (+python-urllib)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# DOM helpers — discover the three selectors we need.
# ---------------------------------------------------------------------------

# JS that runs in the page and returns labeled interactive elements. Each
# element gets an ID we can reference back. We capture the attributes most
# useful for building stable selectors (and for the LLM to reason about).
_ENUMERATE_JS = r"""
() => {
  const wanted = ['textarea', 'input', 'button', '[contenteditable="true"]', '[role="textbox"]', '[role="button"]'];
  const seen = new Set();
  const out = [];
  for (const sel of wanted) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      // Skip offscreen / zero-size elements (hidden helpers).
      if (rect.width === 0 || rect.height === 0) continue;
      const attrs = {};
      for (const a of el.attributes) {
        if (['style', 'src', 'srcset', 'd', 'fill', 'stroke'].includes(a.name)) continue;
        attrs[a.name] = a.value.slice(0, 120);
      }
      out.push({
        tag: el.tagName.toLowerCase(),
        attrs,
        text: (el.innerText || '').trim().slice(0, 80),
        // Whether this button contains an SVG icon (common pattern in modern UIs).
        has_svg: !!el.querySelector('svg'),
        // SVG class names — useful for icon-only buttons (e.g. lucide-send-horizontal).
        svg_classes: Array.from(el.querySelectorAll('svg')).flatMap(s => Array.from(s.classList)).join(' ').slice(0, 120),
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      });
    }
  }
  return out;
}
"""


def _describe_element(idx, el):
    """Short human-readable line for one candidate element."""
    parts = [f"[{idx}] <{el['tag']}"]
    for k, v in el["attrs"].items():
        parts.append(f' {k}="{v}"')
    if el["disabled"]:
        parts.append(" disabled")
    parts.append(">")
    if el["text"]:
        parts.append(f' text="{el["text"]}"')
    if el["has_svg"]:
        parts.append(f' icon-svg-classes="{el["svg_classes"]}"')
    return "".join(parts)


def _selector_for(el):
    """Build a stable CSS selector from an enumerated element's attributes."""
    tag = el["tag"]
    attrs = el["attrs"]
    # 1. id → unique
    if attrs.get("id"):
        return f'#{attrs["id"]}'
    # 2. name → very stable for form fields
    if attrs.get("name"):
        return f'{tag}[name="{attrs["name"]}"]'
    # 3. data-testid → testing-friendly
    if attrs.get("data-testid"):
        return f'{tag}[data-testid="{attrs["data-testid"]}"]'
    # 4. aria-label → semantic
    if attrs.get("aria-label"):
        return f'{tag}[aria-label="{attrs["aria-label"]}"]'
    # 5. SVG icon class (Lucide / Heroicons / etc.) — common for icon buttons
    if el["has_svg"] and el["svg_classes"]:
        # Pick a class that looks like an icon name (e.g. "lucide-send-horizontal").
        for cls in el["svg_classes"].split():
            if "-" in cls and not cls.startswith("lucide") and "icon" not in cls:
                continue
            return f'{tag}:has(svg.{cls})'
        # Fallback: any svg class
        cls = el["svg_classes"].split()[0]
        return f'{tag}:has(svg.{cls})'
    # 6. type attribute (e.g. button[type="submit"])
    if attrs.get("type") and tag in ("button", "input"):
        return f'{tag}[type="{attrs["type"]}"]'
    # 7. Visible text via :has-text — Playwright extension
    if el["text"]:
        return f'{tag}:has-text("{el["text"][:30]}")'
    return tag


def discover_selectors(page):
    """Enumerate page elements, ask LLM which IDs are input/send, build selectors."""
    elements = page.evaluate(_ENUMERATE_JS)
    print(f"  found {len(elements)} interactive element(s) on page")

    if not elements:
        raise RuntimeError("No interactive elements found on the page")

    # Build the labeled list we'll show the LLM.
    listing = "\n".join(_describe_element(i, el) for i, el in enumerate(elements))

    system = (
        "You are looking at a list of interactive elements from a chatbot "
        "website. Each line starts with [ID]. Pick the ID that matches:\n"
        '  "input_id": the chat textbox where the user types their message. '
        "This is usually a <textarea>, <input>, or [contenteditable] element. "
        "It is NOT a search box, NOT a settings field, NOT a username/email "
        "field — it is the main chat message composer.\n\n"
        "Return ONLY a JSON object with one integer field. No prose, no markdown.\n"
        'Example: {"input_id": 3}'
    )
    user = f"Elements:\n{listing}\n\nReturn the JSON now."

    raw = groq_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    try:
        choice = json.loads(raw)
        input_id = int(choice["input_id"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise RuntimeError(f"LLM did not return valid JSON: {raw}") from e

    if not (0 <= input_id < len(elements)):
        raise RuntimeError(f"LLM returned out-of-range input_id: {choice}")

    selectors = {"input": _selector_for(elements[input_id])}

    # Validate: selector should match exactly one element.
    count = page.locator(selectors["input"]).count()
    if count == 0:
        raise RuntimeError(f"Built input selector matches 0 elements: {selectors['input']}")
    if count > 1:
        print(f"  WARNING: input selector matches {count} elements: {selectors['input']}")

    return selectors


# ---------------------------------------------------------------------------
# Prompt loop
# ---------------------------------------------------------------------------

def body_text(page):
    """All visible text on the page — generic baseline for response capture."""
    try:
        return page.locator("body").inner_text(timeout=2_000).strip()
    except Exception:
        return ""


def send_and_capture(page, selectors, prompt_text):
    """Type the prompt, press Enter to send, wait for new stable text, return it."""
    input_sel = selectors["input"]

    # Snapshot what's already on screen — anything *new* after sending is the reply.
    baseline = body_text(page)

    page.wait_for_selector(input_sel, timeout=15_000)
    page.fill(input_sel, prompt_text)
    page.press(input_sel, "Enter")

    deadline = time.monotonic() + RESPONSE_MAX_WAIT_SECONDS
    last_text = ""
    last_change = time.monotonic()

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        current = body_text(page)
        if current != last_text:
            last_text = current
            last_change = time.monotonic()
            continue
        # Content unchanged this tick. If it's stable AND differs from baseline,
        # we've got a reply.
        if current != baseline and (time.monotonic() - last_change) >= STABLE_SECONDS:
            break

    # Diff baseline vs current to find the *new chunk in the middle*. We can't
    # use a set-based "line not in baseline" check because some chatbots
    # (e.g. Glow's canned safety deflection) return the same text repeatedly,
    # and we need to capture each new instance.
    new_lines = _diff_middle(baseline.splitlines(), last_text.splitlines())

    # Drop the prompt itself (it appears in the chat history as the user message).
    new_lines = [ln for ln in new_lines if ln.strip() != prompt_text.strip()]
    # Drop common UI button labels that may appear/disappear with chat state.
    UI_NOISE = {"Clear", "New chat", "New conversation", "Send", "Stop", "Regenerate", "Copy"}
    new_lines = [ln for ln in new_lines if ln.strip() not in UI_NOISE]
    # Drop empty lines.
    new_lines = [ln for ln in new_lines if ln.strip()]

    return "\n".join(new_lines).strip()


def _diff_middle(baseline_lines, current_lines):
    """Return the slice of `current_lines` that's new compared to `baseline_lines`.

    Find the longest common prefix and longest common suffix; anything in
    `current` between those is what changed (the new message + reply).
    Robust to repeated text — unlike a set difference.
    """
    pre = 0
    while (pre < len(baseline_lines) and pre < len(current_lines)
           and baseline_lines[pre] == current_lines[pre]):
        pre += 1

    suf = 0
    # Cap suffix so prefix + suffix can't overlap.
    max_suf = min(len(baseline_lines) - pre, len(current_lines) - pre)
    while (suf < max_suf
           and baseline_lines[-(suf + 1)] == current_lines[-(suf + 1)]):
        suf += 1

    return current_lines[pre:len(current_lines) - suf]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY is not set.")
        print("Get a free key from https://console.groq.com/keys")
        print('Then run: export GROQ_API_KEY="your-key-here"')
        sys.exit(1)

    prompts = load_prompts(PROMPTS_FILE)
    if not prompts:
        print(f"No prompts found in {PROMPTS_FILE}")
        sys.exit(1)

    done_ids = load_done_ids(RESULTS_FILE)
    Path(USER_DATA_DIR).mkdir(exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            channel="chrome",
            headless=False,
            chromium_sandbox=True,
            ignore_default_args=["--no-sandbox", "--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(BASE_URL)

        print("Browser opened.")
        print("Log in if needed, then open the chatbot panel so the chat input is visible.")
        input("Press Enter when the chat input is ready... ")

        print("Asking Groq for the chat selectors on this page...")
        try:
            selectors = discover_selectors(page)
        except Exception as e:
            print(f"ERROR discovering selectors: {e}")
            context.close()
            sys.exit(1)
        print(f"Selectors:")
        for k, v in selectors.items():
            print(f"  {k}: {v}")

        if done_ids:
            print(f"Resuming — {len(done_ids)} prompt(s) already in {RESULTS_FILE}")

        for idx, prompt_text in enumerate(prompts, start=1):
            pid = str(idx)
            if pid in done_ids:
                print(f"[{pid}] skip (already done)")
                continue

            print(f"[{pid}] {prompt_text[:80]}")
            row = {"id": pid, "prompt": prompt_text, "response": "", "status": "OK"}
            try:
                row["response"] = send_and_capture(page, selectors, prompt_text)
                if not row["response"]:
                    row["status"] = "EMPTY"
            except Exception as e:
                row["status"] = "ERROR"
                row["response"] = f"{type(e).__name__}: {e}"
                print(f"  ERROR: {row['response']}")

            append_result(RESULTS_FILE, row)

        print(f"Done. Results in {RESULTS_FILE}")
        input("Browser left open for inspection. Press Enter to close... ")
        context.close()


if __name__ == "__main__":
    main()
