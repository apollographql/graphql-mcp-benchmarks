# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""Benchmark orchestrator — invoked by bench.sh (run stage).

Conditions run in parallel (one thread per condition); tasks and reps within each
condition run sequentially. Each recipe includes a unique @@RUN_ID@@ token in its
instructions block, which changes the Anthropic cache prefix per run and ensures
every rep starts cold — no warm-cache carryover from prior reps of the same task.
Each run gets its own proxy port (BASE_PORT + condition_index) and its own
GOOSE_LOG_DIR under run_dir, so parallel conditions never share mutable state.

For each (condition, task, rep) it runs in isolation:
  1. render the condition's recipe template -> runs/<cond>/<task>/rep<k>/recipe.yaml
  2. clear stale Goose llm_request logs (per-run dir)
  3. start the logging proxy with a per-run PROXY_LOG, wait for health
  4. `goose run --recipe ...` (headless), capturing stdout
  5. stop the proxy, snapshot Goose's llm_request logs, write meta.json

Proxy output (runs/<cond>/<task>/rep<k>/proxy.jsonl) is the primary measurement;
the Goose snapshot (goose_llm_request.*.jsonl) is the cross-check. Everything is
parameterized via env (see bench.sh / .env.example).

Matrix:
  A1  REST,  GITHUB_TOOLSETS=default                    (headline)
  A2  REST,  GITHUB_TOOLSETS=repos,issues,pull_requests (sensitivity)
  B   GraphQL via Apollo MCP
  B2  GraphQL via Rover Schema MCP
  C   GraphQL via rover CLI (only if ENABLE_ROVER=1; reported separately)

Filtering:
  CONDITIONS=A1,A2   run only these conditions (default: all)
  TASKS=T1,T2        run only these tasks      (default: all)
"""
import json
import os
import shutil
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
# Goose does not honour GOOSE_LOG_DIR; it always writes llm_request.*.jsonl
# to this XDG path. We clear it before each run and copy after.
GOOSE_LOG_DIR = Path.home() / ".local/state/goose/logs"
APOLLO_BIN = str((ROOT / env("APOLLO_BIN", "bin/apollo-mcp-server")).resolve())
APOLLO_CONFIG = str((ROOT / env("APOLLO_CONFIG", "config/apollo-mcp.github.local.yaml")).resolve())
B2_BIN = str((ROOT / env("B2_BIN", "bin/rover-schema-mcp")).resolve())
B2_SDL = str((ROOT / env("B2_SDL", "config/github.graphql")).resolve())
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

# condition -> (recipe template, {config}). `toolsets` fills the GitHub MCP
# --toolsets flag: A1 = all (the server's out-of-box default), A2 = minimal.
CONDITIONS = {
    "A1": ("recipe_rest.yaml", {"toolsets": "all"}),
    "A2": ("recipe_rest.yaml", {"toolsets": "repos,issues,pull_requests"}),
    "B":  ("recipe_graphql.yaml", {}),
    "B2": ("recipe_graphql_b2.yaml", {}),
}
if ENABLE_ROVER:
    CONDITIONS["C"] = ("recipe_rover.yaml", {})


def render_task(prompt_tmpl: str) -> str:
    return (prompt_tmpl
            .replace("{{repo}}", REPO)
            .replace("{{window_start}}", WINDOW_START)
            .replace("{{window_end}}", WINDOW_END)
            .replace("{{file_path}}", FILE_PATH)).strip()


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


def clear_goose_logs():
    if GOOSE_LOG_DIR.is_dir():
        for f in GOOSE_LOG_DIR.glob("llm_request*.jsonl"):
            try:
                f.unlink()
            except OSError:
                pass


def snapshot_goose_logs(run_dir: Path) -> int:
    n = 0
    if GOOSE_LOG_DIR.is_dir():
        for f in sorted(GOOSE_LOG_DIR.glob("llm_request*.jsonl")):
            shutil.copy2(f, run_dir / ("goose_" + f.name))
            n += 1
    return n


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
    run_dir = RUNS_DIR / cond / task["id"] / f"rep{rep}"
    run_dir.mkdir(parents=True, exist_ok=True)
    label = f"{cond}/{task['id']}/rep{rep}"

    task_prompt = render_task(task["prompt"])
    tokens = {
        "@@MODEL@@": MODEL or "claude-sonnet-4-6",
        "@@APOLLO_BIN@@": APOLLO_BIN,
        "@@APOLLO_CONFIG@@": APOLLO_CONFIG,
        "@@GH_TOOLSETS@@": cond_env.get("toolsets", "all"),
        "@@B2_BIN@@": B2_BIN,
        "@@B2_SDL@@": B2_SDL,
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
    clear_goose_logs()

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

    n_goose_files = snapshot_goose_logs(run_dir)
    n_messages = count_messages(proxy_log)
    meta = {
        "condition": cond, "task_id": task["id"], "task_title": task["title"], "rep": rep,
        "repo": REPO, "window_start": WINDOW_START, "window_end": WINDOW_END,
        "file_path": FILE_PATH, "model": MODEL or "claude-sonnet-4-6 (recipe default)",
        "toolsets": cond_env.get("toolsets"),
        "goose_exit": goose_exit, "timed_out": timed_out, "budget_killed": budget_killed,
        "started": started, "ended": time.time(), "duration_s": round(time.time() - started, 1),
        "n_proxy_messages": n_messages, "n_goose_log_files": n_goose_files,
        "rotation_truncated": n_messages > 10 and n_goose_files >= 10,
        "recipe": str(recipe_path),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    flags = []
    if meta["rotation_truncated"]:
        flags.append("ROTATION-TRUNCATED goose logs; proxy authoritative")
    if meta["budget_killed"]:
        flags.append(f"BUDGET-KILLED at ${_running_cost_usd(proxy_log, run_model):.3f}")
    flag_str = (" [" + "; ".join(flags) + "]") if flags else ""
    run_cost = _running_cost_usd(proxy_log, run_model)
    print(f"    done: {n_messages} inference calls, exit={goose_exit}, "
          f"{meta['duration_s']}s ${run_cost:.4f}{flag_str}", flush=True)
    return meta


def main():
    if not TASKS_YAML.exists():
        sys.exit(f"missing {TASKS_YAML}")
    tasks = yaml.safe_load(TASKS_YAML.read_text())["tasks"]
    if ONLY_TASKS:
        tasks = [t for t in tasks if t["id"] in ONLY_TASKS]
    if not tasks:
        ids = [t["id"] for t in yaml.safe_load(TASKS_YAML.read_text())["tasks"]]
        sys.exit(f"no tasks selected (TASKS={ONLY_TASKS}, available in yaml: {','.join(ids)})")
    conds = [c for c in CONDITIONS if not ONLY or c in ONLY]
    if not conds:
        sys.exit(f"no conditions selected (CONDITIONS={ONLY}, available={list(CONDITIONS)})")

    base_env = dict(os.environ)
    base_env.setdefault("GOOSE_PROVIDER", "anthropic")
    # Ensure ./bin (apollo-mcp-server) is reachable for condition B.
    base_env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{base_env.get('PATH', '')}"

    total = len(conds) * len(tasks) * REPS
    smoke_note = f" [SMOKE MODE: model={MODEL}, reps={REPS}]" if SMOKE else ""
    par_note = f" [parallel: {len(conds)} condition(s)]" if len(conds) > 1 else ""
    print(f"Matrix: conditions={conds} tasks={[t['id'] for t in tasks]} reps={REPS} "
          f"→ {total} runs | repo={REPO} window={WINDOW_START}..{WINDOW_END}"
          f"{smoke_note}{par_note}\n")

    base_port = int(PORT)
    all_results: list = []
    results_lock = threading.Lock()
    budget_stop = threading.Event()
    budget_exc: list = []  # holds at most one BudgetExhausted

    def run_condition(cond: str, port: str, index: int = 0):
        if index > 0 and STAGGER_S > 0:
            time.sleep(index * STAGGER_S)
        print(f"Condition {cond} (port {port}):")
        for task in tasks:
            for rep in range(1, REPS + 1):
                if budget_stop.is_set():
                    return
                meta = run_one(cond, task, rep, base_env, port=port)
                with results_lock:
                    all_results.append(meta)

    if len(conds) == 1:
        # Single condition: run sequentially, no threading overhead.
        try:
            run_condition(conds[0], PORT)
        except BudgetExhausted as e:
            print(f"\n\n*** BUDGET EXHAUSTED — matrix halted after {len(all_results)} run(s) ***")
            print(f"    {e}")
            (RUNS_DIR / "_index.json").write_text(json.dumps(all_results, indent=2))
            sys.exit(1)
    else:
        # Multiple conditions: run each condition in its own thread.
        # Tasks/reps within each condition remain sequential so cache-hit patterns
        # from prior reps are preserved. Each condition gets its own proxy port
        # (base_port + index) and its own GOOSE_LOG_DIR so they never share state.
        with ThreadPoolExecutor(max_workers=len(conds)) as executor:
            futures = {
                executor.submit(run_condition, cond, str(base_port + i), i): cond
                for i, cond in enumerate(conds)
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
