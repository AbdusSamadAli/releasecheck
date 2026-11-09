# ReleaseCheck: Check Your App Before You Ship It

ReleaseCheck looks inside **your own app build** (a `.zip`, `.apk`, `.jar`, `.whl` or similar) before
release. It tells you in plain English whether it is ready: **PASS**, **PASS WITH NOTES** or **FAIL**.

Built with **MCP** (its own server, plus **REA** as an optional second server), **LangChain**,
**LangGraph**, **Groq** and **Streamlit**.

---

## Aim

Give developers a fast, trustworthy check of what is **actually inside the package they ship**,
with clear evidence for every problem and a human making the final decision.

## The problem

The final app package often contains things nobody meant to ship:

- a secret API key or password copied from a config or `.env` file
- debug or developer mode still switched on
- test or local addresses such as `http://localhost:3000`
- developer-only files such as `.env`, `.map` source maps or logs
- test, mock or fake modes left on (for example fake payments)
- TODO notes for unfinished features

Teams usually check their **source code**, but users download the **build**. Checking the
build by hand is slow and easy to forget, and simply asking an LLM "is my app OK?" is
unreliable because it can make things up.

## The solution

ReleaseCheck works like a careful teammate:

1. **Open** the build. It is unpacked and read only, never executed.
2. **Checklist:** fixed rules from `checklist.json` find known problems. The result is the same every time.
3. **Deep look (optional):** an AI agent reads the files through MCP tools (plus REA's tools when enabled)
   and spots problems the rules cannot, for example `payments: { mode: 'fake' }`.
4. **Verify:** plain Python drops any AI finding whose quoted evidence is not really in the file.
5. **Explain:** the AI writes a plain-English summary and a note for each item.
6. **Review:** you untick anything that is not a real problem.
7. **Report:** PASS / FAIL, saved to history and downloadable as Markdown.

**Rule:** *tools find the facts, the AI explains them, and a human decides.*

| Priority | Meaning | Example |
|---|---|---|
| Must fix | Do not release until fixed | Secret key in the app |
| Should fix | Affects users, fix soon | Debug mode on, test address |
| Nice to fix | Tidy-up | TODO notes, very large files |

Any accepted **Must fix** gives **FAIL**. Other accepted items give **PASS WITH NOTES**, and no items gives **PASS**.

---

## How MCP, LangChain and LangGraph are used

### MCP (Model Context Protocol)

- **Own server:** `releasecheck_server.py`, built with the `mcp` v2 Python SDK (`MCPServer`), over stdio.
  - **Tools:** `open_build`, `list_files`, `read_file`, `search_text`, `get_checklist`, `run_checklist`, `save_report`, `get_history`
  - **Resource:** `report://{report_id}`
  - **Prompt:** `release_summary`
- **Client side:** `mcp_bridge.py` opens MCP sessions with `mcp.Client`, discovers tools at runtime and
  wraps each one as a LangChain `StructuredTool`. Every step of the workflow talks to the server through MCP.

### REA (Reverse Engineer Anything)

[REA](https://github.com/morluto/rea) is an open-source MCP server with tools for looking inside
compiled software (binaries, Electron/JS apps, APKs).

- Turn it on with `REA_ENABLED=true`. The bridge starts it with `REA_COMMAND` (default `npx -y @morluto/rea mcp`).
- Its tools are loaded automatically, prefixed `rea_`, and given to the deep-look agent alongside the
  ReleaseCheck tools.
- It is optional. If REA is off or fails to start, the agent continues with the built-in tools and the
  step log says why.
- REA needs Node.js 18+. It works locally and in the Docker image, but not on Streamlit Community Cloud,
  which has no Node.js.
- Use it only on software you own or are authorised to analyse. The app asks for this confirmation before every check.

### LangChain

- `ChatGroq` (`langchain-groq`) with the `openai/gpt-oss-120b` model.
- `bind_tools` gives the MCP tools (wrapped as `StructuredTool`) to the agent.
- Pydantic models (`Extra`, `Summary`) define the output, and a small helper asks for JSON, validates it and retries once.

### LangGraph

```
START -> open_build -> run_checklist --deep?--> explore -> verify -> explain -> review -> report -> END
                                   \---- no ----------------/
```

- A `StateGraph` with typed state, a conditional edge for the optional deep look, and an
  `operator.add` step log that streams live to the UI.
- `interrupt()` in `review` pauses the graph for the human. `Command(resume=...)` continues it.
- The `MemorySaver` checkpointer keeps each run's state across the pause.

---

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
python samples/make_samples.py
streamlit run app.py
```

On Windows use `copy .env.example .env`, then put your Groq key in `.env`. Get a free key at https://console.groq.com/keys.

| Command | Purpose |
|---|---|
| `python cli.py samples/demo_app.zip --deep` | Terminal version |
| `python evaluate.py` | Known-answer test of the checklist (no AI) |
| `python evaluate.py --ai` | Also tests that the AI deep look finds the hidden issue |

### How to test

- **Demo app (has problems)** gives **FAIL** with about 8 items. With the deep look on, it also finds "Fake payment mode".
- **Clean app (should pass)** gives **PASS**.
- Untick the secret-key rows in the review table and the result becomes **PASS WITH NOTES**.
- Or upload a zip of your own project or build output.

Evaluation result: checklist recall **5/5 (100%)** with **0 false alarms**, and the deep look catches the
hidden fake-payments setting.


## Run with Docker (full version including REA)

```bash
docker compose up --build
```

Open http://localhost:8501.

- **`Dockerfile`:** what goes inside the image. Python 3.12 slim, Node.js for REA, the libraries and code,
  a non-root user and a health check.
- **`docker-compose.yml`:** how the container runs. Port 8501, the `.env` settings passed in at run time,
  a volume so report history survives restarts, a 1 GB memory limit and automatic restart.
- Smaller image without Node: `docker build --build-arg WITH_NODE=false -t releasecheck .`

---

## Configuration (`.env` or Streamlit secrets)

| Name | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | none | Turns on the AI summary and the deep look |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model |
| `REA_ENABLED` | `false` | Load REA tools into the deep look |
| `REA_COMMAND` | `npx -y @morluto/rea mcp` | How to start REA |
| `MAX_UPLOAD_MB` | `50` | Upload size limit |
| `EXPLORE_STEPS` | `6` | Maximum agent tool rounds |

The Groq free tier allows about 200k tokens per day per model, and one deep look uses roughly 15–25k.
Without the AI, the app still finishes with a built-in summary.

## Safety

- Builds are only unpacked and read, never run.
- Unpacking rejects paths that would land outside the workspace and enforces size and file-count limits.
- Secrets are masked in the UI and report (`sk_t****`).
- AI findings must pass a plain Python evidence check, and a human approves every item.
- With the deep look on, file contents are sent to Groq. Leave it off for builds that contain real secrets.

## Project structure

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `graph.py` | LangGraph workflow |
| `mcp_bridge.py` | MCP client and LangChain tool wrapper (ReleaseCheck + REA) |
| `releasecheck_server.py` | Own MCP server (8 tools, 1 resource, 1 prompt) |
| `build_tools.py` | Unpacking, checklist, evidence check, history |
| `checklist.json` | The release rules (edit to add your own) |
| `config.py` | Settings from `.env` or Streamlit secrets |
| `cli.py` | Terminal runner |
| `evaluate.py` | Known-answer evaluation |
| `samples/` | Demo app with planted fake problems, clean app, expected results |
| `Dockerfile`, `docker-compose.yml` | Container setup |
