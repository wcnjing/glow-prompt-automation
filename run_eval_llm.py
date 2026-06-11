"""
LLM-driven prompt evaluation using Browser Use + Groq (Llama 3.3 70B).

This is the experimental, product-agnostic version. The LLM drives the browser
based on what it sees on screen, so it works on any chatbot UI without
hard-coded selectors.

Setup:
    pip install browser-use
    playwright install chromium
    export GROQ_API_KEY="your-key-from-console.groq.com/keys"

Run:
    python run_eval_llm.py
"""

import asyncio
import csv
import os
import re
import sys
from pathlib import Path

# Disable Browser Use's anonymized telemetry (it spams SSL errors at shutdown).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

# Config — change BASE_URL per product
BASE_URL = "https://stg-glow.edutech.works/login"
PROMPTS_FILE = "prompts.txt"
RESULTS_FILE = "results_llm.csv"
USER_DATA_DIR = "browser_session"
MODEL_NAME = "llama-3.3-70b-versatile"   # Groq's most capable general-purpose model. Alternatives: "llama-3.1-70b-versatile", "qwen-2.5-32b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

CSV_FIELDS = ["id", "prompt", "response", "status"]


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


async def run_prompt(browser_session, llm, prompt_text):
    """Have the LLM type the prompt, wait for the response, and return it."""
    from browser_use import Agent

    task = (
        "The chatbot website is already loaded in the browser and the user is "
        "already logged in. Do NOT navigate to any URL. Stay on the current page.\n\n"
        "Old messages from a previous task may be visible above the chat input. "
        "Do not read or summarize those old messages. They are not relevant.\n\n"
        f"Step 1: find the chat input box at the bottom of the page.\n"
        f'Step 2: type this exact message into it: "{prompt_text}"\n'
        f"Step 3: send the message by clicking the send button or pressing Enter.\n"
        f"Step 4: wait for the chatbot to finish replying. The reply is finished "
        f"when no new text has appeared for several seconds.\n"
        f"Step 5: read the full text of the new reply that appeared after your "
        f"message, and return it as your final answer. Then stop.\n\n"
        "Do not ask follow-up questions. Do not include old messages in your answer."
    )
    agent = Agent(task=task, llm=llm, browser_session=browser_session)
    history = await agent.run()

    final = history.final_result() if hasattr(history, "final_result") else None
    if not final:
        # Surface the failure so we don't write empty OK rows.
        print(f"    (agent returned no final result; check logs above)")
        raise RuntimeError("Agent finished without a captured response")
    return final


async def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY is not set.")
        print("Get a free key from https://console.groq.com/keys")
        print('Then run: export GROQ_API_KEY="your-key-here"')
        sys.exit(1)

    try:
        from browser_use import BrowserSession
        from browser_use.llm import ChatOpenAI
    except ImportError as e:
        print(f"ERROR: {e}")
        print("Install with: pip install browser-use")
        sys.exit(1)

    prompts = load_prompts(PROMPTS_FILE)
    if not prompts:
        print(f"No prompts found in {PROMPTS_FILE}")
        sys.exit(1)

    done_ids = load_done_ids(RESULTS_FILE)
    Path(USER_DATA_DIR).mkdir(exist_ok=True)

    llm = ChatOpenAI(
        model=MODEL_NAME,
        base_url=GROQ_BASE_URL,
        api_key=os.environ["GROQ_API_KEY"],
        # Groq doesn't support OpenAI's strict structured-output mode.
        # Tell Browser Use to inject the schema into the prompt instead.
        dont_force_structured_output=True,
        add_schema_to_system_prompt=True,
    )
    browser_session = BrowserSession(
        user_data_dir=str(Path(USER_DATA_DIR).resolve()),
        headless=False,
    )
    await browser_session.start()

    page = await browser_session.get_current_page()
    await page.goto(BASE_URL)

    print("Browser opened. Log in and open the chatbot if needed.")
    input("Press Enter when the chat input is visible and ready...")

    for idx, prompt_text in enumerate(prompts, start=1):
        pid = str(idx)
        if pid in done_ids:
            print(f"[{pid}] skip (already done)")
            continue

        print(f"[{pid}] {prompt_text[:80]}")
        row = {"id": pid, "prompt": prompt_text, "response": "", "status": "OK"}
        try:
            row["response"] = await run_prompt(browser_session, llm, prompt_text)
        except Exception as e:
            row["status"] = "ERROR"
            row["response"] = f"{type(e).__name__}: {e}"
            print(f"  ERROR: {row['response']}")

        append_result(RESULTS_FILE, row)

    print(f"Done. Results in {RESULTS_FILE}")
    input("Browser left open for inspection. Press Enter to close...")
    await browser_session.close()


if __name__ == "__main__":
    asyncio.run(main())
