# Glow Prompt Evaluation Tool

A Playwright-based tool that automates sending a list of prompts to a chatbot
and saves the responses to a CSV file.

This repo has **two scripts** that do roughly the same job:

- **`run_eval.py`** — the original. Hand-coded CSS selectors for Glow.
  Reliable and fast for Glow specifically.
- **`run_eval_llm_v2.py`** — product-agnostic. Uses an LLM (Groq's Llama 3.3)
  to identify the chat input on whatever chatbot is loaded, then drives it
  with Playwright. Slower to set up, works on chatbots the original script
  doesn't know about. Use this if you want to evaluate other MOE chatbot
  products beyond Glow.

Both write to CSV with the same columns.

---

## Step 1 — One-time setup

You only need to do this once on your machine.

### Requirements

- Mac, Linux, or Windows with Python 3.10 or newer
- Google Chrome installed (the real one, not Chromium)
- A Glow account you can log into with Google

### Install (Mac / Linux)

Open a terminal in this project folder and run:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Install (Windows)

Open PowerShell or Command Prompt in this project folder and run:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

That's it. The `venv/` folder is your isolated Python environment. You
never touch it directly.

---

## Step 2 — Add your prompts

Open `prompts.txt` in any text editor and write **one prompt per line**.
Blank lines are ignored. Numbering at the start of a line (like `1.` or
`2)`) is optional — it'll be stripped automatically.

Example:

```
What is photosynthesis?
Explain long division using 144 divided by 12.
How does the water cycle work?
```

Save the file. You can edit it anytime between runs.

---

## Step 3 — Run the tool

In the same terminal:

**Mac / Linux:**
```bash
source venv/bin/activate    # only needed once per terminal session
python run_eval.py
```

**Windows:**
```powershell
venv\Scripts\activate
python run_eval.py
```

A Chrome window will open.

**First time:**
- The terminal prints `Not logged in. Complete Google login...`
- Sign in with Google in the Chrome window.
- **Open the chatbot panel manually** — click into the chat UI so the
  message input is visible on screen. The script waits for this and won't
  start sending prompts until the chat is open.
- The script auto-detects when the chat input appears and starts sending
  prompts.
- Your login is saved in `browser_session/` so you won't have to log in again.

**Every other time:**
- The terminal prints `Already logged in — starting.` (only after you open
  the chatbot panel — same as above, you still need to click into the chat).
- The script begins as soon as the chat input is visible.

> **Important:** the script does not auto-open the chatbot. You always need
> to click into the chat UI yourself before the prompt loop will start.

While it runs you'll see lines like:
```
[1] What is photosynthesis...
    submitted, waiting for typing indicator...
[2] Explain long division...
```

When all prompts are done:
```
Done. Results in results.csv
Browser left open for inspection. Press Enter to close...
```

Press Enter in the terminal to close the browser.

---

## Step 4 — Look at your results

Open `results.csv` in Excel, Google Sheets, or Numbers. Columns:

| Column | Meaning |
|---|---|
| `id` | The line number of the prompt in `prompts.txt` |
| `prompt` | The prompt you sent |
| `response` | What the AI replied |
| `status` | `OK` or `ERROR` (with the error message in `response`) |

---

## Running again

To re-run the same prompts from scratch:

**Mac / Linux:**
```bash
rm results.csv
python run_eval.py
```

**Windows:**
```powershell
del results.csv
python run_eval.py
```

If you don't delete `results.csv`, the script **resumes**. It skips any
prompt IDs already in the file. Useful if a long run crashed halfway.

To **swap accounts** (use a different Google login):

**Mac / Linux:**
```bash
rm -rf browser_session/
python run_eval.py
```

**Windows:**
```powershell
rmdir /s /q browser_session
python run_eval.py
```

The next run will prompt you to log in again.

---

## Alternative: the LLM-driven script (`run_eval_llm_v2.py`)

Use this when you want to test a chatbot that **isn't Glow** — the LLM reads
the page and figures out which element is the chat input, so no per-product
selector tweaking.

### One-time setup (in addition to Step 1 above)

1. Get a free Groq API key at <https://console.groq.com/keys>.
2. In your terminal, export it (keys are sensitive — never paste into chat or
   commit to git):

   **Mac / Linux:**
   ```bash
   export GROQ_API_KEY="gsk_your-key-here"
   ```

   To make it permanent, add the same line to `~/.zshrc` and run `source ~/.zshrc`.

   **Windows (PowerShell):**
   ```powershell
   $env:GROQ_API_KEY = "gsk_your-key-here"
   ```

3. Open `run_eval_llm_v2.py` and change `BASE_URL` to the chatbot you want to
   test. Defaults to Glow staging.

### Run

```bash
source venv/bin/activate
python run_eval_llm_v2.py
```

What you'll see:

1. A Chrome window opens.
2. Log in if needed, then open the chatbot panel so the chat input is visible.
3. Press Enter in the terminal.
4. The script asks Groq once: "which element here is the chat input?" and
   prints the selector it picked.
5. It then loops through `prompts.txt`, typing each prompt and pressing Enter
   to send. Responses go to `results_llm.csv`.

### How it's different

| | `run_eval.py` | `run_eval_llm_v2.py` |
|---|---|---|
| Works on | Glow only | Any chatbot |
| Setup per product | Update CSS selectors | None |
| Sends via | Click submit button | Press Enter |
| Response capture | Glow's response spans | Text-diff before vs after send |
| External services | None | Groq API (free tier) |
| Output file | `results.csv` | `results_llm.csv` |

### Troubleshooting (LLM script)

| Problem | Try this |
|---|---|
| `CERTIFICATE_VERIFY_FAILED` on a corporate network | `pip install truststore` (already in `requirements.txt`). The script auto-detects it and uses your system trust store. |
| `HTTP 403` from Groq | Cloudflare blocked the request. The script sends a normal User-Agent header to avoid this — if you still see it, your `GROQ_API_KEY` is wrong or revoked. |
| `HTTP 413 Payload Too Large` | The trimmed HTML is too big. Open the script and lower `max_chars` in `trim_html` (currently 8000). |
| The script types but never sends | The chatbot may not accept Enter to send. For Glow specifically, use `run_eval.py` instead — it clicks the send button. |
| Responses come out empty | The text-diff isn't finding new content. Usually because the chatbot's UI doesn't update its DOM in a way the diff catches. Try `run_eval.py` if you're on Glow. |

---

## Configuration (advanced)

Open `run_eval.py`. The top of the file has all the knobs you might want
to tweak:

| Constant | What it controls |
|---|---|
| `BASE_URL` | Which Glow environment (staging, dev, prod). |
| `PROMPTS_FILE` / `RESULTS_FILE` | File names if you want different ones. |
| Five `*_SELECTOR` constants | DOM selectors. Update these if Glow's UI changes. |
| `RESPONSE_STABLE_MS` | How long the AI must stop typing before we consider the answer complete (default 6 seconds). Increase if responses are getting cut off. |
| `RESPONSE_MAX_WAIT_MS` | Hard ceiling per prompt (default 4 minutes). |
| `CLEAR_BETWEEN_PROMPTS` | Whether to click "Clear" between prompts. |

---

## Troubleshooting

| Problem | Try this |
|---|---|
| Google says *"Couldn't sign you in — this browser may not be secure"* | The script already patches the obvious automation flags. If still blocked, run `pip install playwright-stealth` and ask whoever set this up to wire it in. |
| AI keeps returning the same canned `"I'm here to help you understand the learning topics..."` for everything | That's Glow's safety/scope filter for off-topic or sensitive prompts — it's the *real* response, not a bug. |
| Script hangs after a prompt finishes | The typing indicator isn't detaching cleanly. Try increasing `RESPONSE_STABLE_MS` in the script. |
| Answers are cut short in the CSV | Same fix — bump `RESPONSE_STABLE_MS` higher (the AI is pausing longer than 6s between chunks). |
| Wrong Google account auto-loaded | `rm -rf browser_session/` and re-run to force a fresh login. |

---

## Files in this folder

| File | What it is |
|---|---|
| `run_eval.py` | Glow-specific Playwright script. The reliable default. |
| `run_eval_llm_v2.py` | Product-agnostic Playwright + LLM script. Needs `GROQ_API_KEY`. |
| `prompts.txt` | Your prompts (edit this). Used by both scripts. |
| `results.csv` | Output from `run_eval.py` (not in git). |
| `results_llm.csv` | Output from `run_eval_llm_v2.py` (not in git). |
| `requirements.txt` | Python packages to install. |
| `README.md` | This file. |
| `.gitignore` | Tells git what to ignore. |
| `venv/` | Python virtual environment (created by setup, not in git). |
| `browser_session/` | Saved Chrome login (created on first run, not in git). |
