# Xlator → Claude Agent SDK Integration Spec

**Audience:** engineer migrating an existing web app from a Claude Code **CLI subprocess**
(`claude -p …` with the vendored `xl-plugin` installed) to the **Python Claude Agent SDK**
(`claude-agent-sdk`), driven by an `ANTHROPIC_API_KEY`.

**Interaction model:** mostly autonomous, with graceful handling of the occasional
interactive prompt (`AskUserQuestion` in `convert-doc`; `:::user_input` fences elsewhere).

> ⚠️ **API-surface caveat.** The SDK's Python symbol names and option signatures evolve.
> Every code block below is *shape-accurate, not version-pinned*. Pin your
> `claude-agent-sdk` version and verify each option (`setting_sources`, `plugins`,
> `agents`, `hooks`, `can_use_tool`) against that version's reference before shipping.
> Where a name is uncertain it is flagged inline.

---

## 0. Why this migration is small

The plugin's coupling to the Claude Code *runtime* (vs. the API) is concentrated in a few
places. The rest is plain Markdown skills + a pure-CLI Python toolchain that runs identically
under any harness.

| Coupling point | Where | Migration action |
|---|---|---|
| Built-in tools (Read/Write/Edit/Bash/Glob/Grep) | every skill | **None** — SDK provides them |
| `xlator` bash shim + `uv run` Python tools (24.8k LOC, tested) | every skill | **None** — invoked via Bash exactly as today |
| `:::` fence output protocol | all 23 skills | Re-point your existing fence parser at the SDK stream |
| Env vars (`DOMAINS_DIR`, `DOMAINS_FULLPATH`, `CLAUDE_PLUGIN_ROOT`, `XLATOR_AI_CONCURRENCY`) | all 23 skills | Provide via `options.env` + on-PATH `xlator` |
| CLAUDE.md behavioral contract | `xl-plugin/CLAUDE.md`, root `CLAUDE.md` | Inject into `system_prompt` (or `setting_sources`) |
| **Subagent** `index-inputs-worker` | `index-inputs` only | **Define programmatically** (`.agent.md` not auto-loaded by SDK) |
| `AskUserQuestion` | `convert-doc` only | `can_use_tool` callback |
| `:::user_input` text prompts | a handful of skills' pre-flight | Fence-parser resolver (auto-answer or surface to UI) |

Only the **last three rows** require new code beyond configuration.

---

## 1. Target architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Web app backend (Python)                                     │
│                                                              │
│  request ──▶ XlatorSession                                   │
│               │  - per-session workspace (domains/ + git)    │
│               │  - ClaudeAgentOptions (plugin, env, agents,  │
│               │    hooks, can_use_tool)                      │
│               │                                              │
│               ├─▶ claude_agent_sdk.query(prompt, options) ───┼──▶ Anthropic API
│               │      stream of messages                      │      (ANTHROPIC_API_KEY)
│               │        │                                     │
│               │        ├─ assistant text ─▶ FenceParser ─▶ UI route
│               │        ├─ tool_use events ─▶ progress UI     │
│               │        └─ AskUserQuestion ─▶ can_use_tool ──▶ UI prompt
│               ▼                                              │
│  Sandbox: opam/catala/clerk + uv/python + git + xlator shim  │
└─────────────────────────────────────────────────────────────┘
```

Three things the harness owns that Claude Code used to own for you:

1. **The execution sandbox** — `catala`/`clerk` (opam), `uv`/Python 3.14, `git`, and the
   `xlator` shim, all on `PATH`. Reuse `repo-template/.devcontainer/Dockerfile` verbatim;
   it already builds `catala.1.1.0` + `catala-lsp` + `catala-format` + `uv`. (Drop the
   `claude.ai/install.sh` line — you no longer need the CLI.)
2. **A per-session writable workspace** — the pipeline is filesystem-centric (the `domains/`
   tree, `git hash-object` change detection, on-disk manifests). Each session needs an
   isolated working tree with a real git repo.
3. **The conversation loop + I/O routing** — replaces the single `claude -p` call.

---

## 2. Environment & the `xlator` shim

The skills assume the exact environment `xlator_setup.sh` produces. Replicate it; do **not**
re-run the Claude-Code-specific parts of that script (marketplace install, etc.).

Required env (set on `options.env` *and* exported for the Bash sandbox):

| Var | Value | Used by |
|---|---|---|
| `DOMAINS_DIR` | path to domains folder, **relative to project root** | every skill |
| `DOMAINS_FULLPATH` | absolute `$PROJECT_ROOT/$DOMAINS_DIR` | `xlator` shim (`cd`s here) |
| `CLAUDE_PLUGIN_ROOT` | absolute path to vendored `xl-plugin/` | skills reading `core/*.md`, `cp` templates |
| `XLATOR_AI_CONCURRENCY` | `3` default; raise per rate-limit headroom | `index-inputs` fan-out |
| `ANTHROPIC_API_KEY` | your key | SDK auth |

The `xlator` shell shim (`xl-plugin/bin/xlator`) must be on `PATH` inside the sandbox so
`Bash` calls like `xlator compress-inputs <domain> --plan` resolve. It reads
`.xlator.local.env` from the project root, activates opam, and delegates to `tools/*.py` via
`uv run`. **Action:** symlink/copy `xl-plugin/bin/xlator` to a PATH dir, and write
`.xlator.local.env` at the workspace root with the vars above (mirror the `set -o allexport`
block the shim sources at line 30).

> The shim already handles "Claude Code does not source shell config" (it runs
> `eval "$(opam env)"` itself), so it behaves identically under the SDK sandbox.

---

## 3. SDK configuration (`ClaudeAgentOptions`)

```python
from pathlib import Path
from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition  # names: verify vs version

PLUGIN_ROOT = Path("/srv/app/vendor/xl-plugin")          # vendored plugin
WORKSPACE   = Path("/srv/sessions/<session_id>")          # per-session tree, git-init'd
DOMAINS_DIR = "domains"

def build_options(domains_dir: str = DOMAINS_DIR) -> ClaudeAgentOptions:
    project_root = WORKSPACE
    domains_fullpath = project_root / domains_dir

    return ClaudeAgentOptions(
        cwd=str(domains_fullpath),     # matches xlator shim's `cd "$DOMAINS_FULLPATH"`
        env={
            "DOMAINS_DIR": domains_dir,
            "DOMAINS_FULLPATH": str(domains_fullpath),
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
            "XLATOR_AI_CONCURRENCY": "3",
            "PATH": f"{PLUGIN_ROOT}/bin:" + os.environ["PATH"],   # put `xlator` on PATH
        },

        # 1) Load the vendored plugin → registers its 23 skills + hooks.json.
        #    Skills become namespaced as `xl:<skill-name>` (e.g. xl:index-inputs).
        plugins=[{"type": "local", "path": str(PLUGIN_ROOT)}],

        # 2) Load project CLAUDE.md + settings (optional; see §4 for the alternative).
        setting_sources=["project"],

        # 3) Behavioral contract the skills assume (fencing, uv/xlator rules, AskUserQuestion
        #    fallback). Append the plugin CLAUDE.md text — see §4.
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": _xlator_system_append()},

        # 4) Tools the skills actually use. Skill + Task/Agent must be allowed.
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep",
                       "Skill", "Task", "AskUserQuestion"],

        # 5) Autonomous by default; gate dangerous ops via can_use_tool (§6) not prompts.
        permission_mode="acceptEdits",   # writes within workspace; pair with sandbox (§7)

        # 6) The one subagent, defined programmatically (§5).
        agents={"index-inputs-worker": _index_inputs_worker_agent()},

        # 7) Observability hooks — keep or re-wire (§8).
        # hooks=...,  # see §8

        # 8) can_use_tool callback for AskUserQuestion + risky Bash (§6).
        # can_use_tool=...,  # see §6
    )
```

Key points:

- **`plugins` is the spine.** Pointing at the vendored `xl-plugin` dir (which contains
  `.claude-plugin/plugin.json`) registers all skills and `hooks/hooks.json` in the format the
  repo already ships. This is what makes plugin *updates* cheap: re-vendor the directory, no
  re-port.
- **`Task`/`Agent` must be allowed** so `index-inputs` can fan out to the worker subagent.
- **`permission_mode="acceptEdits"`** keeps it autonomous for file writes; the Bash sandbox
  (§7) is your real safety boundary.

---

## 4. Injecting the CLAUDE.md behavioral contract

Claude Code auto-loads `CLAUDE.md` files; the SDK does so only via `setting_sources`. The
skills depend on `xl-plugin/CLAUDE.md` (fence protocol, "run Python via `uv`/`xlator`",
`AskUserQuestion` fallback rules, env-var conventions) and parts of the root `CLAUDE.md`.

Two equivalent options — pick one and be explicit:

- **A (recommended, deterministic):** read the files and append them to `system_prompt`:

  ```python
  def _xlator_system_append() -> str:
      parts = [
          (PLUGIN_ROOT / "CLAUDE.md").read_text(),
          # Root CLAUDE.md sections that affect runtime behavior (shell portability,
          # uv usage, fencing). Trim project-governance sections (versioning, git rules)
          # that don't apply to an autonomous web run.
      ]
      return "\n\n".join(parts)
  ```

- **B (rely on loader):** set `setting_sources=["project"]` and place the vendored
  `xl-plugin/CLAUDE.md` so the loader picks it up. Less explicit about *which* guidance is
  active; verify your SDK version actually loads plugin-level CLAUDE.md (plugin CLAUDE.md
  loading is **not guaranteed** across versions — prefer A if unsure).

`core/output-fencing.md`, `core/ruleset-shared.md`, the Catala quickrefs, etc. are read
**on demand by the skills via the Read tool** — they only need to exist on disk under
`CLAUDE_PLUGIN_ROOT`. No injection required.

---

## 5. The subagent (`index-inputs-worker`)

`index-inputs` Step 5 dispatches `subagent_type: index-inputs-worker` in bounded batches of
`XLATOR_AI_CONCURRENCY`. The SDK does **not** auto-load `agents/*.agent.md`, so port the
agent file's frontmatter + body into a programmatic `AgentDefinition`:

```python
def _index_inputs_worker_agent() -> AgentDefinition:
    body = (PLUGIN_ROOT / "agents" / "index-inputs-worker.agent.md").read_text()
    # Strip the YAML frontmatter; the body is the system prompt for the subagent.
    prompt = body.split("---", 2)[-1].strip()
    return AgentDefinition(
        description=("Per-file worker for /index-inputs: runs compress + extract for one "
                     "source doc end-to-end."),
        prompt=prompt,
        tools=["Read", "Write", "Skill"],   # mirrors the .agent.md `tools:` line
        model="inherit",
    )
```

Notes / gotchas:

- Keep this in sync with the `.agent.md` file. A drift check: read both at startup and log if
  the body diverges from what you ported. (This is the one recurring maintenance seam.)
- **`AskUserQuestion` is unavailable inside subagents** — fine here; the worker never prompts
  (it writes `succeeded:`/`failed:` lines and surfaces errors via markers).
- The worker invokes child skills (`/compress-input`, `/extract-computations`) via the `Skill`
  tool, so `Skill` must be in its `tools` list (it is).
- Batch parallelism: the orchestrator issues K parallel `Task` calls per turn. Your rate-limit
  exposure scales with K — keep `XLATOR_AI_CONCURRENCY=3` until your tier headroom is confirmed.

---

## 6. Interactive prompts: `AskUserQuestion` + `:::user_input`

Two distinct prompt channels; handle both.

### 6a. `AskUserQuestion` (used by `convert-doc`)

Wire a `can_use_tool` callback. When `tool_name == "AskUserQuestion"`, pause the agent, surface
the structured `questions` to your UI, and return the user's selections:

```python
async def can_use_tool(tool_name, tool_input, context):
    if tool_name == "AskUserQuestion":
        # tool_input["questions"]: [{question, header, options[], multiSelect}, ...]
        answers = await surface_questions_to_ui(tool_input["questions"])  # blocks on user
        return {"behavior": "allow",
                "updatedInput": {**tool_input, "answers": answers}}   # shape: verify vs version
    # Autonomous default for everything else:
    return {"behavior": "allow"}
```

For **mostly-autonomous** runs where no human is attached, return a deterministic default or a
`deny` with a clear message so the run fails loudly rather than hanging.

### 6b. `:::user_input` fences (pre-flight prompts in several skills)

Some skills (e.g. `index-inputs` domain-selection, the git-commit confirmation) emit a
`:::user_input` **text fence** instead of calling `AskUserQuestion` — the CLAUDE.md fallback
contract. The agent then *waits for the next user turn*. To stay autonomous:

1. **Pre-empt them.** Always pass fully-qualified prompts (`/index-inputs snap`, never
   `/index-inputs`) so the argument pre-flight is satisfied and the prompt never fires.
2. **Resolve at the stream.** If a `:::user_input` block appears, your FenceParser (§9)
   detects it; either auto-answer per a policy table (e.g. answer the "commit uncommitted
   input files?" prompt with `n`) by sending a follow-up turn via `ClaudeSDKClient`, or surface
   it to the UI and resume. Use the CLAUDE.md interpretation rules: a single matching option
   letter selects it; any longer string is a verbatim custom answer.

> Run with `ClaudeSDKClient` (not one-shot `query()`) when you need this resume-with-answer
> round-trip — it keeps the session open for follow-up turns.

---

## 7. Bash sandbox & file safety

- The SDK runs `Bash` in an OS sandbox (macOS Seatbelt / Linux bubblewrap). Configure
  filesystem write access to the **session workspace** (the `domains/` tree, `policy_facets/`,
  `specs/`, `output/`) and read access to `CLAUDE_PLUGIN_ROOT`.
- The pipeline shells out to `git`, `opam`, `catala`, `clerk`, `uv` — all child processes
  inherit the sandbox, so confirm those binaries' working dirs are within `allowWrite`
  (e.g. `_build/` under `specs/`/`output/` per the clerk bootstrap note in the project CLAUDE.md).
- `clerk`/`catala` resolve the stdlib via `./_build/libcatala` relative to CWD — the skills
  already `cd` into `specs/`/`output/` and call `clerk_loop.ensure_catala_bootstrap(cwd)`.
  Ensure the sandbox permits writing `_build/` there.
- Network: the toolchain is offline at runtime once installed; lock the sandbox network to the
  Anthropic API domain only.

---

## 8. Observability hooks (`hooks.json`)

`hooks/hooks.json` registers `xlator observe_hook <Event>` on PostToolUse(Bash/Write/Edit/
AskUserQuestion), SessionStart/End, Stop, UserPromptSubmit, logging to `*/logs/session.jsonl`.

Options:

- **Keep as-is:** loaded automatically with the plugin (`plugins=[…]`). Shell-command hooks are
  honored by the SDK for events it supports. Verify `SessionStart`/`SessionEnd`/`Stop`/
  `UserPromptSubmit` are all available in your SDK version (some hook events have been
  TypeScript-only at points — **check**). The `SessionStart` entry also runs
  `./xlator_setup.sh`; **drop or replace that command** — your backend provisions the env (§2),
  you don't want the marketplace-install path firing.
- **Re-wire as callbacks:** if shell hooks are flaky in your version, register programmatic
  hooks (`PreToolUse`/`PostToolUse`) that call `observe_hook.py` directly via `uv run`. Same
  JSONL output, fewer moving parts.

`OBSERVE_HOOK_DISABLED=1` (read by the shim) turns logging off cleanly if you'd rather observe
from the SDK stream instead.

---

## 9. Fence parser & stream routing

Replace the "read stdout of `claude -p`" logic with an async consumer of the SDK message
stream. For each assistant text delta, run the **same** `:::` parser your current harness uses
(the protocol is unchanged):

- Open on a line that is exactly `:::type`; close on a line that is exactly `:::`. No nesting.
- Route by type: `important`→primary result, `error`→failure (skill stopped), `next_step`→
  workflow suggestions, `detail`→collapsible technical output, `progress`→transient status,
  `user_input`→prompt (see §6b). Unfenced text → `detail`.
- **Known edge case (carry forward):** verbatim relay output containing a bare `:::` line
  (e.g. a test runner) will be mis-read as a close delimiter. Document it; the project already
  flags this in `core/output-fencing.md`.

Also surface SDK `tool_use`/`tool_result` events to drive a live activity view (which Bash/Skill
is running) — richer than the old subprocess stdout gave you.

---

## 10. Invoking a workflow

A skill is triggered by instructing the model to use it. With plugin namespacing, the skill is
`xl:<name>`. For deterministic, autonomous runs send an explicit directive as the prompt:

```python
from claude_agent_sdk import query

async for message in query(
    prompt="Run the /index-inputs skill for domain `snap`. Use defaults for any prompts.",
    options=build_options(),
):
    route(message)   # → FenceParser + tool-event UI
```

For multi-step workflows (index → refine-guidance → extract-ruleset), drive them as sequential
turns on one `ClaudeSDKClient` session so context (and the git workspace state) carries over,
checking each skill's `:::important`/`:::error` terminal block before issuing the next.

---

## 11. Migration delta from the `claude -p` subprocess

| Today (`claude -p`) | After (SDK) |
|---|---|
| Install plugin via marketplace; spawn `claude` binary | `pip install claude-agent-sdk`; `plugins=[{type:local}]` |
| Plugin skills auto-discovered by CLI | Auto-discovered from `plugins=` (skills + hooks); **subagent ported to `agents=`** |
| CLAUDE.md auto-loaded by CLI | Injected via `system_prompt` append (§4) |
| Env from shell / `xlator_setup.sh` | `options.env` + on-PATH `xlator` + `.xlator.local.env` (§2) |
| Parse CLI stdout for `:::` fences | Parse SDK message stream (§9) — same parser |
| Interactive prompts blocked / unsupported | `can_use_tool` (AskUserQuestion) + fence resolver (§6) |
| `SessionStart` hook ran `xlator_setup.sh` | Backend provisions env; **remove that hook command** (§8) |

What you **delete:** the `claude` binary dependency, marketplace install steps, and any stdout
scraping hacks. What you **keep unchanged:** the entire `tools/` Python layer, the `xlator`
shim, the Catala toolchain, and your fence parser.

---

## 12. Validation plan

Smoke-test end-to-end before cutover, in a throwaway session workspace:

1. **Env sanity:** in the sandbox, `xlator list` returns domain/module pairs; `clerk --version`
   and `catala --version` resolve.
2. **No-AI tools:** `xlator compress-inputs <domain> --plan` returns valid JSON (proves shim +
   env + uv).
3. **Single skill, no prompts:** `query("/index-inputs <domain>")` on a domain with committed
   inputs → expect `:::important` completion, `policy_facets/` populated, exit-clean finalize.
4. **Subagent fan-out:** confirm the worker batches run (tool-event view shows parallel `Task`
   calls) and per-file markers/manifests are written.
5. **Prompt paths:** (a) run `convert-doc` to exercise the `AskUserQuestion`→`can_use_tool`
   round-trip; (b) run `/index-inputs` with *uncommitted* inputs to exercise the
   `:::user_input` git-commit prompt and your resolver.
6. **Full pipeline:** `new-domain → index-inputs → refine-guidance → extract-ruleset →
   create-tests`, then `xlator catala-pipeline <domain> <module>` green.
7. **Regression suite:** run the plugin's own tests (`uv run pytest` under `tools/`) in the
   sandbox image — proves the toolchain layer is intact independent of the SDK.

---

## 13. Risks & ongoing maintenance seams

- **SDK ↔ plugin version independence.** Pin `claude-agent-sdk`; after each plugin re-vendor,
  re-run §12 steps 3–6 as a smoke gate. This is the main recurring cost — low, but non-zero.
- **Subagent drift (§5).** If a plugin update adds/changes `agents/*.agent.md`, you must mirror
  it into `agents=`. Add the startup drift-check log.
- **Hook-event availability (§8).** Re-verify supported hook events on SDK upgrades; have the
  callback fallback ready.
- **Fence `:::` collision (§9).** Pre-existing limitation; unchanged by the migration.
- **Rate limits.** `XLATOR_AI_CONCURRENCY` is the only backpressure on `index-inputs` fan-out;
  surface 429s and auto-lower if needed.
- **Plugin CLAUDE.md loading is not guaranteed (§4).** Prefer the explicit `system_prompt`
  append over relying on `setting_sources` to pick up plugin-level CLAUDE.md.
```
