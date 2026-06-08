# Glow Prompt Evaluation Tool

A simple Playwright-based tool that automates sending a list of prompts to
Glow's AI tutor and saves the responses to a CSV file.

---

## Step 1 — One-time setup

You only need to do this once on your machine.

### Requirements

- Mac or Linux with Python 3.10 or newer
- Google Chrome installed (the real one, not Chromium)
- A Glow account you can log into with Google

### Install

Open a terminal in this project folder and run:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

That's it. The `venv/` folder is your isolated Python environment — you
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

```bash
source venv/bin/activate    # only needed once per terminal session
python run_eval.py
```

A Chrome window will open.

**First time:**
- The terminal prints `Not logged in. Complete Google login...`
- Sign in with Google in the Chrome window.
- The script auto-detects when you're logged in and starts sending prompts.
- Your login is saved in `browser_session/` so you won't have to log in again.

**Every other time:**
- The terminal prints `Already logged in — starting.`
- The script begins immediately.

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

```bash
rm results.csv
python run_eval.py
```

If you don't delete `results.csv`, the script **resumes** — it skips any
prompt IDs already in the file. Useful if a long run crashed halfway.

To **swap accounts** (use a different Google login):

```bash
rm -rf browser_session/
python run_eval.py
```

The next run will prompt you to log in again.

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
| `run_eval.py` | The script. |
| `prompts.txt` | Your prompts (edit this). |
| `results.csv` | Output (created by the script, not in git). |
| `requirements.txt` | Python packages to install. |
| `README.md` | This file. |
| `.gitignore` | Tells git what to ignore. |
| `venv/` | Python virtual environment (created by setup, not in git). |
| `browser_session/` | Saved Chrome login (created on first run, not in git). |
