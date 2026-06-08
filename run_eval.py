import csv
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_URL = "https://stg-glow.edutech.works/login"
PROMPTS_FILE = "prompts.txt"
RESULTS_FILE = "results.csv"
USER_DATA_DIR = "browser_session"

TEXTAREA_SELECTOR = 'textarea[name="query"]'
SUBMIT_SELECTOR = 'button:has(svg.lucide-send-horizontal)'
TYPING_SELECTOR = 'span.typing-dots'
RESPONSE_SELECTOR = 'span.prose.prose-slate'
CLEAR_SELECTOR = 'button:has-text("Clear")'

TYPING_APPEAR_TIMEOUT_MS = 10_000
TYPING_DETACH_TIMEOUT_MS = 180_000
RESPONSE_STABLE_MS = 6_000        # joined response text must be unchanged this long
RESPONSE_MAX_WAIT_MS = 240_000    # overall ceiling per prompt

CLEAR_BETWEEN_PROMPTS = False     # set True only if Clear works without breaking next prompt

CSV_FIELDS = ["id", "prompt", "response", "status"]


def load_prompts(path):
    prompts = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            cleaned = re.sub(r'^\s*\d+\s*[\.\)]\s*', '', line)
            prompts.append(cleaned)
    return prompts


def load_done_ids(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("id"):
                done.add(str(row["id"]))
    return done


def append_result(path, row):
    file_exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _ui_busy(page):
    """Return True if the UI clearly indicates the AI is still working.
    Only signal we trust: typing-dots visible. (Submit button disabled is
    ambiguous — it's also disabled when the textarea is empty.)
    """
    try:
        return page.locator(TYPING_SELECTOR).count() > 0
    except Exception:
        return None


def _all_response_texts(page):
    """List of inner_text for every response span currently in the DOM."""
    responses = page.locator(RESPONSE_SELECTOR)
    total = responses.count()
    out = []
    for i in range(total):
        try:
            out.append(responses.nth(i).inner_text().strip())
        except Exception:
            out.append("")
    return out


def _collect_new_response(page, baseline_texts):
    """Join texts that weren't present at baseline. Robust to chat clears."""
    parts = [t for t in _all_response_texts(page) if t and t not in baseline_texts]
    return "\n\n".join(parts)


def run_prompt(page, prompt_text):
    # Snapshot texts already on screen — anything new will be the response.
    baseline_texts = set(_all_response_texts(page))

    page.wait_for_selector(TEXTAREA_SELECTOR, timeout=15_000)
    page.fill(TEXTAREA_SELECTOR, prompt_text)
    page.click(SUBMIT_SELECTOR)
    print("    submitted, waiting for typing indicator...")

    # Wait for typing-dots to appear (signals AI started).
    try:
        page.wait_for_selector(
            TYPING_SELECTOR, state="attached", timeout=TYPING_APPEAR_TIMEOUT_MS
        )
    except PWTimeout:
        print("    (typing indicator never appeared — proceeding anyway)")

    # Loop: wait for detach, then verify it stays detached for RESPONSE_STABLE_MS.
    # If it re-attaches within that window, the AI resumed streaming — loop.
    deadline = page.evaluate("Date.now()") + RESPONSE_MAX_WAIT_MS
    while page.evaluate("Date.now()") < deadline:
        try:
            page.wait_for_selector(
                TYPING_SELECTOR, state="detached", timeout=TYPING_DETACH_TIMEOUT_MS
            )
        except PWTimeout:
            break  # gave up waiting for typing to stop
        try:
            page.wait_for_selector(
                TYPING_SELECTOR, state="attached", timeout=RESPONSE_STABLE_MS
            )
            continue  # re-attached → AI kept going → wait for next detach
        except PWTimeout:
            break  # stayed detached long enough → done

    text = _collect_new_response(page, baseline_texts)
    if text:
        return text
    # Fallback: just grab the last response span on screen.
    all_texts = _all_response_texts(page)
    return all_texts[-1] if all_texts else ""


def clear_chat(page):
    try:
        page.click(CLEAR_SELECTOR, timeout=5_000)
    except PWTimeout:
        pass


def main():
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

        try:
            page.wait_for_selector(TEXTAREA_SELECTOR, timeout=8_000)
            print("Already logged in — starting.")
        except PWTimeout:
            print("Not logged in. Complete Google login in the open browser...")
            page.wait_for_selector(TEXTAREA_SELECTOR, timeout=300_000)
            print("Login detected — starting.")

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
                row["response"] = run_prompt(page, prompt_text)
            except Exception as e:
                row["status"] = "ERROR"
                row["response"] = f"{type(e).__name__}: {e}"
                print(f"[{pid}] ERROR: {row['response']}")

            append_result(RESULTS_FILE, row)
            clear_chat(page)

        print(f"Done. Results in {RESULTS_FILE}")
        input("Browser left open for inspection. Press Enter to close...")
        context.close()


if __name__ == "__main__":
    main()
