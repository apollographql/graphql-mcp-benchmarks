# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""Benchmark orchestrator — invoked by bench.sh (run stage).

Conditions run in parallel (one thread per condition); tasks and reps within each
condition run sequentially. Each recipe includes a unique @@RUN_ID@@ token in its
instructions block, which changes the Anthropic cache prefix per run and ensures
every rep starts cold — no warm-cache carryover from prior reps of the same task.
Each run gets its own proxy port (BASE_PORT + condition_index) and its own proxy
log, so parallel conditions share no mutable measurement state.

For each (condition, task, rep) it runs in isolation:
  1. render the condition's recipe template -> runs/<cond>/<task>/rep<k>/recipe.yaml
  2. start the logging proxy with a per-run PROXY_LOG, wait for health
  3. `goose run --recipe ...` (headless), capturing stdout
  4. stop the proxy, write meta.json

Proxy output (runs/<cond>/<task>/rep<k>/proxy.jsonl) is the measurement, and the
only one. The Goose `llm_request.*.jsonl` snapshot used to be copied in as a
cross-check; it was retired in phase 2 (PHASE2_PLAN.md §8.2) because Goose
ignores GOOSE_LOG_DIR and writes to one XDG path that every parallel condition
shared *and cleared*, so the column recorded which condition cleared the
directory last rather than corroborating anything. Everything is parameterized
via env (see bench.sh / .env.example).

Phase-1 matrix — GitHub's live API, tasks T*:
  A1  REST,  GITHUB_TOOLSETS=default                    (headline)
  A2  REST,  GITHUB_TOOLSETS=repos,issues,pull_requests (sensitivity)
  B   GraphQL via Apollo MCP
  B2  GraphQL via Rover Schema MCP
  C   GraphQL via rover CLI (only if ENABLE_ROVER=1; reported separately)

Phase-2 matrix — the synthetic three-service stack in services/, tasks M*:
  M-R1  REST,    one tool per endpoint (front-loaded)
  M-R2  REST,    rest_request + openapi_search + openapi_describe (on-demand)
  M-G1  GraphQL, graphql_execute + schema_search + schema_describe (on-demand)
  M-G2  GraphQL, seven frozen persisted operations (front-loaded)

The two phases never mix: a condition serves one backend, so phase-2 conditions
are paired with `phase: 2` tasks only.

Phase-2 tasks are swept over N, and each (task, N) is its own task id — `M3@20`
— so the runs/<cond>/<task>/rep<k> layout needs no new dimension. The task list
and every prompt substitution come from tasks/expected.json, the artifact that
also computes the graded answer; see PHASE2_PLAN.md §7.1 for why those cannot be
allowed to drift apart.

Filtering:
  CONDITIONS=A1,A2      run only these conditions (default: all)
  TASKS=T1,T2           run only these tasks      (default: all)
  TASKS=M3              expands to every M3 cell (M3@5, M3@20, M3@50)
  PAYLOAD_PROFILE=lean  which profile the REST stack is serving (see below)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("PROJECT_ROOT", os.getcwd())).resolve()


def env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


SMOKE = env("SMOKE", "0") == "1"  # fast/cheap harness validation: 1 rep, Haiku, no T3
REPO = env("REPO", "graphql/graphql-js")
WINDOW_START = env("WINDOW_START", "2026-03-01")
WINDOW_END = env("WINDOW_END", "2026-05-31")
FILE_PATH = env("FILE_PATH", "src/execution/execute.ts")
# SMOKE overrides: Haiku, 1 rep, T1+T2 only (T3 is where cost explodes)
MODEL = env("MODEL", "claude-haiku-4-5-20251001" if SMOKE else "")
REPS = int(env("REPS", "1" if SMOKE else "3"))
PORT = env("PORT", "8080")
MAX_TURNS = env("MAX_TURNS", "50")
RUN_TIMEOUT = int(env("RUN_TIMEOUT", "420"))
ENABLE_ROVER = env("ENABLE_ROVER", "0") == "1"
# Which payload profile the phase-2 REST stack is serving. The REST services read
# PAYLOAD_PROFILE at container start, so this is a property of the running stack,
# not something the runner can switch per condition — `services_up()` asserts the
# stack agrees rather than trusting the variable.
PAYLOAD_PROFILE = env("PAYLOAD_PROFILE", "fat")
APOLLO_BIN = str((ROOT / env("APOLLO_BIN", "bin/apollo-mcp-server")).resolve())
APOLLO_CONFIG = str((ROOT / env("APOLLO_CONFIG", "config/apollo-mcp.github.local.yaml")).resolve())
B2_BIN = str((ROOT / env("B2_BIN", "servers/rover_schema_mcp.py")).resolve())
B2_SDL = str((ROOT / env("B2_SDL", "config/github.graphql")).resolve())
# Phase-2 tool surfaces (§8.1). The two Python servers resolve their own spec/SDL
# paths relative to themselves, so they need no arguments beyond --mode.
OPENAPI_MCP = str((ROOT / env("OPENAPI_MCP", "servers/openapi_mcp.py")).resolve())
SUPERGRAPH_MCP = str((ROOT / env("SUPERGRAPH_MCP", "servers/supergraph_mcp.py")).resolve())
APOLLO_PHASE2_CONFIG = str(
    (ROOT / env("APOLLO_PHASE2_CONFIG", "config/apollo-mcp.phase2.local.yaml")).resolve()
)
ONLY = [c.strip() for c in env("CONDITIONS", "").split(",") if c.strip()]   # optional condition filter
ONLY_TASKS = [t.strip() for t in env("TASKS", "").split(",") if t.strip()]  # optional task filter
# Per-run cost ceiling in USD. When > 0, the runner kills Goose mid-run if the
# running cost (read from proxy.jsonl every POLL_INTERVAL seconds) exceeds this
# value and marks the run budget_killed=True. 0 = disabled.
PER_RUN_BUDGET_USD = float(env("PER_RUN_BUDGET_USD", "0"))
POLL_INTERVAL = 5  # seconds between cost checks when PER_RUN_BUDGET_USD is set
# When conditions run in parallel, stagger their start times by this many seconds
# (condition_index * STAGGER_S). Gives the first condition time to warm the prompt
# cache before the next one starts, reducing cache_creation races at peak concurrency.
STAGGER_S = float(env("STAGGER_S", "10"))

# Per-model pricing for the mid-run cost check (USD per 1M tokens).
# Keep in sync with parse_logs.py _PRICING.
_RUN_PRICING = [
    ("claude-haiku-4-5",  {"input": 1.00, "output":  5.00, "cache_create": 1.25, "cache_read": 0.10}),
    ("claude-sonnet-4-6", {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}),
    ("claude-opus-4",     {"input": 5.00, "output": 25.00, "cache_create": 6.25, "cache_read": 0.50}),
]
_RUN_PRICE_FALLBACK = {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}


def _price_for_model(model: str) -> dict:
    for prefix, p in _RUN_PRICING:
        if (model or "").startswith(prefix):
            return p
    return _RUN_PRICE_FALLBACK


def _running_cost_usd(proxy_log: Path, model: str) -> float:
    p = _price_for_model(model)
    total = 0.0
    if not proxy_log.exists():
        return total
    for line in proxy_log.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("is_messages"):
            continue
        total += (
            (r.get("input_tokens") or 0)              * p["input"]        / 1_000_000
            + (r.get("output_tokens") or 0)           * p["output"]       / 1_000_000
            + (r.get("cache_creation_input_tokens") or 0) * p["cache_create"] / 1_000_000
            + (r.get("cache_read_input_tokens") or 0) * p["cache_read"]   / 1_000_000
        )
    return total

RUNS_DIR = ROOT / "runs"
RECIPES = ROOT / "recipes"
TASKS_YAML = ROOT / "tasks" / "tasks.yaml"
EXPECTED_JSON = ROOT / "tasks" / "expected.json"
SERVICES_DIR = ROOT / "services"
FIXTURE_MANIFEST = SERVICES_DIR / "fixtures" / "manifest.json"

# condition -> (recipe template, {config}).
#
# `phase` decides which backend — and therefore which tasks — a condition serves.
# `toolsets` fills the GitHub MCP --toolsets flag: A1 = all (the server's
# out-of-box default), A2 = minimal.
CONDITIONS = {
    "A1": ("recipe_rest.yaml",       {"phase": 1, "toolsets": "all"}),
    "A2": ("recipe_rest.yaml",       {"phase": 1, "toolsets": "repos,issues,pull_requests"}),
    "B":  ("recipe_graphql.yaml",    {"phase": 1}),
    "B2": ("recipe_graphql_b2.yaml", {"phase": 1}),
}
if ENABLE_ROVER:
    CONDITIONS["C"] = ("recipe_rover.yaml", {"phase": 1})

# Phase 2 — the 2x2 of protocol x tool packaging (PHASE2_PLAN.md §4).
#
# `profiles` lists the stack payload profiles in which a condition runs. The REST
# services read PAYLOAD_PROFILE at container start, so the profile belongs to the
# running stack and the six-cell matrix is two passes over four conditions:
#
#   PAYLOAD_PROFILE=fat  ./bench.sh run    # M-R1, M-R2, M-G1, M-G2
#   PAYLOAD_PROFILE=lean ./bench.sh run    # M-R1, M-R2
#
# The M-G* conditions declare ("fat",) not because they are fat but because a
# GraphQL query names the fields it wants, so the REST profile cannot reach them.
# Running them in both passes would buy 66 identical runs. They record
# profile=None in meta.json, so the report never pairs them into a fat/lean
# bracket — the bracket is REST's, and it is the headline claim (§11).
CONDITIONS.update({
    "M-R1": ("recipe_m_r1.yaml", {"phase": 2, "surface": "rest",    "profiles": ("fat", "lean")}),
    "M-R2": ("recipe_m_r2.yaml", {"phase": 2, "surface": "rest",    "profiles": ("fat", "lean")}),
    "M-G1": ("recipe_m_g1.yaml", {"phase": 2, "surface": "graphql", "profiles": ("fat",)}),
    "M-G2": ("recipe_m_g2.yaml", {"phase": 2, "surface": "graphql", "profiles": ("fat",)}),
})


def cond_phase(cond: str) -> int:
    return CONDITIONS[cond][1].get("phase", 1)


def cond_profile(cond: str):
    """The profile recorded for this condition's runs — None when it cannot apply."""
    return PAYLOAD_PROFILE if CONDITIONS[cond][1].get("surface") == "rest" else None


def render_task(task: dict) -> str:
    """Fully resolve one task's prompt, or die.

    Phase-2 substitutions come from `expected.json[<id>].placeholders`, so the
    artifact that computes the graded answer also decides which records the prompt
    names. Deriving them here instead is how you get a prompt asking about one set
    of flights and a grader scoring another, with every result then wrong in a way
    that looks like agent error (§7.1).

    An unresolved `{{...}}` is fatal rather than a warning: shipping the literal
    text to the model produces a plausible-looking run that measures nothing, and
    it would be indistinguishable from a real answer in the results.
    """
    text = task["prompt"]
    for key, value in (task.get("placeholders") or {}).items():
        text = text.replace(key, value)
    text = (text
            .replace("{{repo}}", REPO)
            .replace("{{window_start}}", WINDOW_START)
            .replace("{{window_end}}", WINDOW_END)
            .replace("{{file_path}}", FILE_PATH)).strip()
    left = sorted(set(re.findall(r"\{\{[^}]*\}\}", text)))
    if left:
        sys.exit(
            f"{task['id']}: unresolved placeholder(s) {', '.join(left)} in the rendered "
            f"prompt.\n"
            f"Phase-2 substitutions come from tasks/expected.json[{task['id']}].placeholders "
            f"(regenerate with `cd services && pnpm expected`); phase-1 ones come from .env."
        )
    return text


def load_tasks() -> list:
    """The task list, with phase-2 entries expanded over N into one task per cell.

    `expected.json` is authoritative — its keys are the ids that actually run —
    and `tasks.yaml`'s `ns:` is a copy. Both are checked against each other here
    rather than trusted, the same way fixtures are checked against their manifest.
    `pnpm test` checks the same agreement from the TypeScript side; this is the
    other end of it, and the one that runs before any money is spent.
    """
    if not TASKS_YAML.exists():
        sys.exit(f"missing {TASKS_YAML}")
    raw = yaml.safe_load(TASKS_YAML.read_text())["tasks"]

    phase2 = [t for t in raw if int(t.get("phase", 1)) == 2]
    expected, sweep = {}, {}
    if phase2:
        if not EXPECTED_JSON.exists():
            sys.exit(f"missing {EXPECTED_JSON} — run `cd services && pnpm expected`")
        expected = json.loads(EXPECTED_JSON.read_text())
        sweep = expected.get("_meta", {}).get("sweep", {})

    tasks = []
    for t in raw:
        phase = int(t.get("phase", 1))
        if phase == 1:
            tasks.append({**t, "phase": 1, "n": None, "cell": t["id"], "placeholders": {}})
            continue

        want = sorted(int(n) for n in (t.get("ns") or []))
        have = sorted(int(n) for n in sweep.get(t["id"], []))
        if want != have:
            sys.exit(
                f"{t['id']}: tasks.yaml says ns={want} but expected.json says {have}.\n"
                f"expected.json is authoritative; `cd services && pnpm expected` regenerates "
                f"it, then fix the `ns:` copy in tasks.yaml to match."
            )
        for n in have:
            cell = f"{t['id']}@{n}"
            if cell not in expected:
                sys.exit(f"{cell}: in _meta.sweep but not a cell of {EXPECTED_JSON}")
            tasks.append({
                **t,
                "id": cell,          # the runs/<cond>/<task>/ directory and raw.csv key
                "cell": cell,
                "base_id": t["id"],
                "phase": 2,
                "n": n,
                "placeholders": expected[cell].get("placeholders") or {},
            })

    # The reverse direction: a cell nobody runs. Catches an expected.json task
    # that was never added to tasks.yaml, which would otherwise just be a graded
    # answer with no run to grade.
    orphans = sorted(set(expected) - {"_meta"} - {t["cell"] for t in tasks})
    if orphans:
        sys.exit(f"{EXPECTED_JSON} has cell(s) with no tasks.yaml entry: {', '.join(orphans)}")

    for t in tasks:
        t["prompt_rendered"] = render_task(t)
    return tasks


def select_tasks(tasks: list) -> list:
    """Apply the TASKS filter. `M3` selects every M3 cell; `M3@20` selects one."""
    if not ONLY_TASKS:
        return tasks
    return [t for t in tasks if t["id"] in ONLY_TASKS or t.get("base_id") in ONLY_TASKS]


def render_recipe(template_text: str, task_prompt: str, tokens: dict) -> str:
    """Inject @@TASK_PROMPT@@ as a block scalar and replace single-line @@TOKEN@@s."""
    out_lines = []
    for line in template_text.splitlines():
        if line.strip() == "@@TASK_PROMPT@@":
            indent = line[: len(line) - len(line.lstrip())]
            for pl in task_prompt.splitlines():
                out_lines.append(indent + pl if pl.strip() else "")
        else:
            for k, v in tokens.items():
                line = line.replace(k, v)
            out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def _expected_matches_fixtures() -> None:
    """Refuse to run when the graded answers were computed from other fixtures.

    `pnpm health` proves the running stack serves the fixtures on disk; this
    proves `expected.json` was generated from those same fixtures. Both halves
    are needed, and this is the invisible one: a stale expected.json grades a
    correct answer as wrong, and nothing about that looks like a data problem in
    the results — it looks like the agent failed.
    """
    if not FIXTURE_MANIFEST.exists():
        sys.exit(f"missing {FIXTURE_MANIFEST} — run `cd services && pnpm fixtures`")
    import hashlib

    on_disk = hashlib.sha256(FIXTURE_MANIFEST.read_bytes()).hexdigest()
    recorded = json.loads(EXPECTED_JSON.read_text()).get("_meta", {}).get("fixtureManifestSha")
    if recorded != on_disk:
        sys.exit(
            f"tasks/expected.json was generated from different fixtures\n"
            f"  expected.json: {str(recorded)[:12]}...\n"
            f"  fixtures now:  {on_disk[:12]}...\n\n"
            f"Regenerate the ground truth: cd services && pnpm expected"
        )


def _assert_symmetric_instructions(conds: list) -> None:
    """The four phase-2 recipes must carry byte-identical `instructions`.

    That block is the system prompt: it enters the cached prefix of every run, so
    a sentence present in one condition and absent from another shifts both the
    token counts and the agent's strategy on one side of a comparison whose whole
    point is the difference between the sides. Phase 1's B/B2 recipes each coach
    their own tool surface, which is why their protocol comparison carries a
    caveat; phase 2 does not get to inherit it.

    Checked here rather than documented in the recipes, because a comment saying
    "keep these identical" is exactly the kind of instruction that loses.
    """
    blocks = {}
    for cond in conds:
        text = (RECIPES / CONDITIONS[cond][0]).read_text()
        if "instructions: |" not in text or "prompt: |" not in text:
            sys.exit(f"{CONDITIONS[cond][0]}: cannot find the instructions block")
        blocks[cond] = text.split("instructions: |", 1)[1].split("prompt: |", 1)[0]
    if len(set(blocks.values())) > 1:
        groups = {}
        for cond, block in blocks.items():
            groups.setdefault(block, []).append(cond)
        variants = " vs ".join("+".join(v) for v in groups.values())
        sys.exit(
            f"phase-2 recipes disagree on their `instructions` block ({variants}).\n"
            f"That block is the system prompt and enters every run's cached prefix, so it "
            f"must be identical across conditions or it becomes part of the measurement."
        )


def services_up(conds: list) -> None:
    """Gate phase-2 runs on the stack being fully up and serving the right profile.

    Delegates to `pnpm health` rather than reimplementing the probes here. That
    script already checks all seven endpoints, proves the router can actually
    reach its subgraphs by issuing a real federated query, verifies fixture
    provenance, and (with --profile) that REST is serving the profile this pass
    claims. A second implementation in Python would be a second thing to keep in
    sync, and the failure that matters is a half-up stack — precisely what a
    simpler probe misses, since an agent reaching two of three services returns a
    confident wrong answer that scores as a cheap success.
    """
    surfaces = {CONDITIONS[c][1].get("surface") for c in conds}
    flags = []
    if surfaces == {"rest"}:
        flags = ["--rest"]
    elif surfaces == {"graphql"}:
        flags = ["--graphql"]
    if "rest" in surfaces:
        flags += ["--profile", PAYLOAD_PROFILE]

    print(f"Phase-2 stack gate: pnpm health {' '.join(flags)}".rstrip(), flush=True)
    try:
        result = subprocess.run(["pnpm", "health", *flags], cwd=str(SERVICES_DIR))
    except FileNotFoundError:
        sys.exit("pnpm not found — phase-2 conditions need `cd services && pnpm install`")
    if result.returncode != 0:
        sys.exit(
            "\nphase-2 stack is not ready (see above):\n\n"
            f"  PAYLOAD_PROFILE={PAYLOAD_PROFILE} docker compose up -d --wait --force-recreate\n"
            "  cd services && pnpm health\n"
        )
    _expected_matches_fixtures()


def wait_health(port: str, timeout=25.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/__health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def count_messages(proxy_log: Path) -> int:
    if not proxy_log.exists():
        return 0
    n = 0
    for line in proxy_log.read_text().splitlines():
        try:
            if json.loads(line).get("is_messages"):
                n += 1
        except json.JSONDecodeError:
            pass
    return n


class BudgetExhausted(Exception):
    pass


def _is_budget_error(text: str) -> bool:
    return "credit balance" in text.lower() or "insufficient_quota" in text.lower()


def run_one(cond: str, task: dict, rep: int, base_env: dict, port: str = PORT) -> dict:
    recipe_tmpl, cond_env = CONDITIONS[cond]
    profile = cond_profile(cond)
    # The profile goes in the directory name so the fat and lean passes cannot
    # overwrite each other's runs. It stays a separate meta.json field because the
    # report wants it as a column, not as part of the condition id — baking it in
    # doubles the width of every table and leaves pairing the bracket, which is
    # the headline claim, to the reader (§11).
    cond_dir = cond if profile is None else f"{cond}-{profile}"
    run_dir = RUNS_DIR / cond_dir / task["id"] / f"rep{rep}"
    run_dir.mkdir(parents=True, exist_ok=True)
    label = f"{cond_dir}/{task['id']}/rep{rep}"

    # Rendered and validated in load_tasks(), before any run started.
    task_prompt = task["prompt_rendered"]
    tokens = {
        "@@MODEL@@": MODEL or "claude-sonnet-4-6",
        "@@APOLLO_BIN@@": APOLLO_BIN,
        "@@APOLLO_CONFIG@@": APOLLO_CONFIG,
        "@@GH_TOOLSETS@@": cond_env.get("toolsets", "all"),
        "@@B2_BIN@@": B2_BIN,
        "@@B2_SDL@@": B2_SDL,
        "@@OPENAPI_MCP@@": OPENAPI_MCP,
        "@@SUPERGRAPH_MCP@@": SUPERGRAPH_MCP,
        "@@APOLLO_PHASE2_CONFIG@@": APOLLO_PHASE2_CONFIG,
        "@@RUN_ID@@": label,  # unique per run — busts Anthropic's prompt cache across reps
    }
    recipe_text = render_recipe((RECIPES / recipe_tmpl).read_text(), task_prompt, tokens)
    recipe_path = run_dir / "recipe.yaml"
    recipe_path.write_text(recipe_text)
    (run_dir / "task_prompt.txt").write_text(task_prompt)

    proxy_log = run_dir / "proxy.jsonl"
    if proxy_log.exists():
        proxy_log.unlink()  # proxy appends — start each run clean so re-runs don't double-count
    run_env = dict(base_env)  # toolsets are baked into the recipe, not env
    run_env["ANTHROPIC_HOST"] = f"http://127.0.0.1:{port}"
    run_env["PROXY_LOG"] = str(proxy_log)
    run_env["RUN_LABEL"] = label
    run_env["PORT"] = str(port)
    # Model is injected into the rendered recipe via @@MODEL@@; no env override needed.

    print(f"  → {label} ...", flush=True)

    proxy = subprocess.Popen(
        ["uv", "run", str(ROOT / "proxy" / "anthropic_logging_proxy.py")],
        cwd=str(ROOT), env=run_env,
        stdout=open(run_dir / "proxy_server.log", "w"), stderr=subprocess.STDOUT,
    )
    started = time.time()
    goose_exit, timed_out, budget_killed = None, False, False
    run_model = MODEL or "claude-sonnet-4-6"
    try:
        if not wait_health(port):
            (run_dir / "stdout.txt").write_text("PROXY DID NOT BECOME HEALTHY\n")
        else:
            cmd = ["goose", "run", "--recipe", str(recipe_path), "--no-session",
                   "--max-turns", str(MAX_TURNS)]
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=run_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.time() + RUN_TIMEOUT
            while proc.poll() is None:
                if time.time() > deadline:
                    proc.kill()
                    timed_out = True
                    break
                if PER_RUN_BUDGET_USD > 0:
                    cost = _running_cost_usd(proxy_log, run_model)
                    if cost > PER_RUN_BUDGET_USD:
                        proc.kill()
                        budget_killed = True
                        print(f"    [BUDGET-KILLED at ${cost:.4f} > ${PER_RUN_BUDGET_USD}]",
                              flush=True)
                        break
                time.sleep(POLL_INTERVAL)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            goose_exit = proc.returncode
            (run_dir / "stdout.txt").write_text(stdout or "")
            (run_dir / "stderr.txt").write_text(stderr or "")

        # Fail-fast: if the Anthropic account is out of credits, stop the matrix.
        combined = ""
        if (run_dir / "stdout.txt").exists():
            combined += (run_dir / "stdout.txt").read_text()
        if (run_dir / "stderr.txt").exists():
            combined += (run_dir / "stderr.txt").read_text()
        if _is_budget_error(combined):
            raise BudgetExhausted(
                f"Credit balance exhausted on {label} — halting matrix. "
                "Top up your Anthropic account (or raise the Console spend limit) and re-run."
            )
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()

    n_messages = count_messages(proxy_log)
    meta = {
        "condition": cond, "task_id": task["id"], "task_title": task["title"], "rep": rep,
        # Phase-2 report axes (§11). `n` is recorded rather than re-split from the
        # task id downstream, and `profile` is None for conditions a REST payload
        # profile cannot reach.
        "phase": task["phase"], "n": task["n"], "profile": profile,
        "repo": REPO, "window_start": WINDOW_START, "window_end": WINDOW_END,
        "file_path": FILE_PATH, "model": MODEL or "claude-sonnet-4-6 (recipe default)",
        "toolsets": cond_env.get("toolsets"), "max_turns": MAX_TURNS,
        "goose_exit": goose_exit, "timed_out": timed_out, "budget_killed": budget_killed,
        "started": started, "ended": time.time(), "duration_s": round(time.time() - started, 1),
        "n_proxy_messages": n_messages,
        "recipe": str(recipe_path),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    flags = []
    if meta["budget_killed"]:
        flags.append(f"BUDGET-KILLED at ${_running_cost_usd(proxy_log, run_model):.3f}")
    flag_str = (" [" + "; ".join(flags) + "]") if flags else ""
    run_cost = _running_cost_usd(proxy_log, run_model)
    print(f"    done: {n_messages} inference calls, exit={goose_exit}, "
          f"{meta['duration_s']}s ${run_cost:.4f}{flag_str}", flush=True)
    return meta


def build_plan(conds: list, tasks: list) -> list:
    """Pair each condition with the tasks it can actually serve.

    A condition serves one backend, so phase-2 conditions run `phase: 2` tasks
    only and phase-1 conditions run `phase: 1` tasks only. Mixing them would send
    a task about synthetic flights to an agent holding GitHub tools, which does
    not error — it produces a confidently wrong answer.
    """
    plan = []
    for cond in conds:
        phase = cond_phase(cond)
        subset = [t for t in tasks if t["phase"] == phase]
        if not subset:
            sys.exit(
                f"{cond} is a phase-{phase} condition but no phase-{phase} task is "
                f"selected (TASKS={ONLY_TASKS or 'all'}). Conditions and tasks must "
                f"belong to the same phase — a condition serves one backend."
            )
        plan.append((cond, subset))
    return plan


def main():
    tasks = select_tasks(load_tasks())
    if not tasks:
        sys.exit(f"no tasks selected (TASKS={ONLY_TASKS}; available: "
                 f"{','.join(t['id'] for t in load_tasks())})")
    conds = [c for c in CONDITIONS if not ONLY or c in ONLY]
    if not conds:
        sys.exit(f"no conditions selected (CONDITIONS={ONLY}, available={list(CONDITIONS)})")

    # A phase-2 pass runs at one payload profile; conditions the profile cannot
    # reach are skipped loudly, because a silent skip reads as a missing result.
    phase2 = [c for c in conds if cond_phase(c) == 2]
    if phase2:
        if PAYLOAD_PROFILE not in ("fat", "lean"):
            sys.exit(f"PAYLOAD_PROFILE={PAYLOAD_PROFILE!r} — expected 'fat' or 'lean'")
        skipped = [c for c in phase2 if PAYLOAD_PROFILE not in CONDITIONS[c][1]["profiles"]]
        if skipped:
            print(f"Skipping {','.join(skipped)} in the {PAYLOAD_PROFILE} pass: a GraphQL "
                  f"query names the fields it wants, so the REST payload profile cannot "
                  f"reach it. Measure those once, in the fat pass.\n")
            conds = [c for c in conds if c not in skipped]
        if not conds:
            sys.exit(f"nothing left to run at PAYLOAD_PROFILE={PAYLOAD_PROFILE}")
        phase2 = [c for c in conds if cond_phase(c) == 2]
        _assert_symmetric_instructions(phase2)
        services_up(phase2)

    plan = build_plan(conds, tasks)

    base_env = dict(os.environ)
    base_env.setdefault("GOOSE_PROVIDER", "anthropic")
    # Ensure ./bin (apollo-mcp-server) is reachable for conditions B and M-G2.
    base_env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{base_env.get('PATH', '')}"

    total = sum(len(subset) for _, subset in plan) * REPS
    smoke_note = f" [SMOKE MODE: model={MODEL}, reps={REPS}]" if SMOKE else ""
    par_note = f" [parallel: {len(conds)} condition(s)]" if len(conds) > 1 else ""
    prof_note = f" profile={PAYLOAD_PROFILE}" if phase2 else ""
    print(f"Matrix: reps={REPS} → {total} runs{prof_note}{smoke_note}{par_note}")
    for cond, subset in plan:
        print(f"  {cond:<5} {len(subset) * REPS:>3} runs  {','.join(t['id'] for t in subset)}")
    if any(cond_phase(c) == 1 for c in conds):
        print(f"  repo={REPO} window={WINDOW_START}..{WINDOW_END}")
    print()

    base_port = int(PORT)
    all_results: list = []
    results_lock = threading.Lock()
    budget_stop = threading.Event()
    budget_exc: list = []  # holds at most one BudgetExhausted

    def run_condition(cond: str, subset: list, port: str, index: int = 0):
        if index > 0 and STAGGER_S > 0:
            time.sleep(index * STAGGER_S)
        print(f"Condition {cond} (port {port}):")
        for task in subset:
            for rep in range(1, REPS + 1):
                if budget_stop.is_set():
                    return
                meta = run_one(cond, task, rep, base_env, port=port)
                with results_lock:
                    all_results.append(meta)

    if len(plan) == 1:
        # Single condition: run sequentially, no threading overhead.
        try:
            run_condition(plan[0][0], plan[0][1], PORT)
        except BudgetExhausted as e:
            print(f"\n\n*** BUDGET EXHAUSTED — matrix halted after {len(all_results)} run(s) ***")
            print(f"    {e}")
            (RUNS_DIR / "_index.json").write_text(json.dumps(all_results, indent=2))
            sys.exit(1)
    else:
        # Multiple conditions: run each condition in its own thread.
        # Tasks/reps within each condition remain sequential so cache-hit patterns
        # from prior reps are preserved. Each condition gets its own proxy port
        # (base_port + index) and writes only into its own run directory.
        with ThreadPoolExecutor(max_workers=len(plan)) as executor:
            futures = {
                executor.submit(run_condition, cond, subset, str(base_port + i), i): cond
                for i, (cond, subset) in enumerate(plan)
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except BudgetExhausted as e:
                    budget_stop.set()
                    budget_exc.append(e)

        if budget_exc:
            print(f"\n\n*** BUDGET EXHAUSTED — matrix halted after {len(all_results)} run(s) ***")
            print(f"    {budget_exc[0]}")
            (RUNS_DIR / "_index.json").write_text(json.dumps(all_results, indent=2))
            sys.exit(1)

    (RUNS_DIR / "_index.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nCompleted {len(all_results)} runs. Index: {RUNS_DIR / '_index.json'}")
    print("Next: parse with  python3 parse_logs.py")


if __name__ == "__main__":
    main()
