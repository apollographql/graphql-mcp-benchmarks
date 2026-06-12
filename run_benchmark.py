# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""Benchmark orchestrator — invoked by bench.sh (run stage).

For each (condition, task, rep) it runs sequentially and in isolation:
  1. render the condition's recipe template -> runs/<cond>/<task>/rep<k>/recipe.yaml
  2. clear stale Goose llm_request logs
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
  C   GraphQL via rover CLI (only if ENABLE_ROVER=1; reported separately)
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("PROJECT_ROOT", os.getcwd())).resolve()


def env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


REPO = env("REPO", "graphql/graphql-js")
WINDOW_START = env("WINDOW_START", "2026-03-01")
WINDOW_END = env("WINDOW_END", "2026-05-31")
FILE_PATH = env("FILE_PATH", "src/execution/execute.ts")
MODEL = env("MODEL", "")  # if set, exported as GOOSE_MODEL (overrides recipe)
REPS = int(env("REPS", "3"))
PORT = env("PORT", "8080")
MAX_TURNS = env("MAX_TURNS", "25")
RUN_TIMEOUT = int(env("RUN_TIMEOUT", "420"))
ENABLE_ROVER = env("ENABLE_ROVER", "0") == "1"
GOOSE_LOG_DIR = Path(env("GOOSE_LOG_DIR", str(Path.home() / ".local/state/goose/logs")))
APOLLO_BIN = str((ROOT / env("APOLLO_BIN", "bin/apollo-mcp-server")).resolve())
APOLLO_CONFIG = str((ROOT / env("APOLLO_CONFIG", "config/apollo-mcp.github.local.yaml")).resolve())
ONLY = [c.strip() for c in env("CONDITIONS", "").split(",") if c.strip()]  # optional filter

RUNS_DIR = ROOT / "runs"
RECIPES = ROOT / "recipes"
TASKS_YAML = ROOT / "tasks" / "tasks.yaml"

# condition -> (recipe template, extra env for goose)
CONDITIONS = {
    "A1": ("recipe_rest.yaml", {"GITHUB_TOOLSETS": "default"}),
    "A2": ("recipe_rest.yaml", {"GITHUB_TOOLSETS": "repos,issues,pull_requests"}),
    "B": ("recipe_graphql.yaml", {}),
}
if ENABLE_ROVER:
    CONDITIONS["C"] = ("recipe_rover.yaml", {})


def render_task(prompt_tmpl: str) -> str:
    return (prompt_tmpl
            .replace("{{repo}}", REPO)
            .replace("{{window_start}}", WINDOW_START)
            .replace("{{window_end}}", WINDOW_END)
            .replace("{{file_path}}", FILE_PATH)).strip()


def render_recipe(template_text: str, task_prompt: str) -> str:
    """Replace @@APOLLO_*@@ scalars and inject @@TASK_PROMPT@@ as a block scalar."""
    out_lines = []
    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped == "@@TASK_PROMPT@@":
            indent = line[: len(line) - len(line.lstrip())]
            for pl in task_prompt.splitlines():
                out_lines.append(indent + pl if pl.strip() else "")
        else:
            out_lines.append(line
                             .replace("@@APOLLO_BIN@@", APOLLO_BIN)
                             .replace("@@APOLLO_CONFIG@@", APOLLO_CONFIG))
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


def wait_health(timeout=25.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{PORT}/__health"
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


def run_one(cond: str, task: dict, rep: int, base_env: dict) -> dict:
    recipe_tmpl, cond_env = CONDITIONS[cond]
    run_dir = RUNS_DIR / cond / task["id"] / f"rep{rep}"
    run_dir.mkdir(parents=True, exist_ok=True)
    label = f"{cond}/{task['id']}/rep{rep}"

    task_prompt = render_task(task["prompt"])
    recipe_text = render_recipe((RECIPES / recipe_tmpl).read_text(), task_prompt)
    recipe_path = run_dir / "recipe.yaml"
    recipe_path.write_text(recipe_text)
    (run_dir / "task_prompt.txt").write_text(task_prompt)

    proxy_log = run_dir / "proxy.jsonl"
    run_env = dict(base_env)
    run_env.update(cond_env)
    run_env["ANTHROPIC_HOST"] = f"http://127.0.0.1:{PORT}"
    run_env["PROXY_LOG"] = str(proxy_log)
    run_env["RUN_LABEL"] = label
    run_env["PORT"] = str(PORT)
    if MODEL:
        run_env["GOOSE_MODEL"] = MODEL

    print(f"  → {label} ...", flush=True)
    clear_goose_logs()

    proxy = subprocess.Popen(
        ["uv", "run", str(ROOT / "proxy" / "anthropic_logging_proxy.py")],
        cwd=str(ROOT), env=run_env,
        stdout=open(run_dir / "proxy_server.log", "w"), stderr=subprocess.STDOUT,
    )
    started = time.time()
    goose_exit, timed_out = None, False
    try:
        if not wait_health():
            (run_dir / "stdout.txt").write_text("PROXY DID NOT BECOME HEALTHY\n")
        else:
            cmd = ["goose", "run", "--recipe", str(recipe_path), "--no-session",
                   "--max-turns", str(MAX_TURNS)]
            try:
                proc = subprocess.run(cmd, cwd=str(ROOT), env=run_env,
                                      capture_output=True, text=True, timeout=RUN_TIMEOUT)
                goose_exit = proc.returncode
                (run_dir / "stdout.txt").write_text(proc.stdout)
                (run_dir / "stderr.txt").write_text(proc.stderr)
            except subprocess.TimeoutExpired as e:
                timed_out = True
                (run_dir / "stdout.txt").write_text((e.stdout or b"").decode("utf-8", "replace")
                                                     if isinstance(e.stdout, bytes) else (e.stdout or ""))
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
        "toolsets": cond_env.get("GITHUB_TOOLSETS"),
        "goose_exit": goose_exit, "timed_out": timed_out,
        "started": started, "ended": time.time(), "duration_s": round(time.time() - started, 1),
        "n_proxy_messages": n_messages, "n_goose_log_files": n_goose_files,
        "rotation_truncated": n_messages > 10 and n_goose_files >= 10,
        "recipe": str(recipe_path),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    flag = " [ROTATION-TRUNCATED goose logs; proxy authoritative]" if meta["rotation_truncated"] else ""
    print(f"    done: {n_messages} inference calls, exit={goose_exit}, "
          f"{meta['duration_s']}s{flag}", flush=True)
    return meta


def main():
    if not TASKS_YAML.exists():
        sys.exit(f"missing {TASKS_YAML}")
    tasks = yaml.safe_load(TASKS_YAML.read_text())["tasks"]
    conds = [c for c in CONDITIONS if not ONLY or c in ONLY]
    if not conds:
        sys.exit(f"no conditions selected (CONDITIONS={ONLY}, available={list(CONDITIONS)})")

    base_env = dict(os.environ)
    base_env.setdefault("GOOSE_PROVIDER", "anthropic")
    # Ensure ./bin (apollo-mcp-server) is reachable for condition B.
    base_env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{base_env.get('PATH', '')}"

    total = len(conds) * len(tasks) * REPS
    print(f"Matrix: conditions={conds} tasks={[t['id'] for t in tasks]} reps={REPS} "
          f"→ {total} runs | repo={REPO} window={WINDOW_START}..{WINDOW_END}\n")

    results = []
    for cond in conds:
        print(f"Condition {cond}:")
        for task in tasks:
            for rep in range(1, REPS + 1):
                results.append(run_one(cond, task, rep, base_env))
    (RUNS_DIR / "_index.json").write_text(json.dumps(results, indent=2))
    print(f"\nCompleted {len(results)} runs. Index: {RUNS_DIR / '_index.json'}")
    print("Next: parse with  python3 parse_logs.py")


if __name__ == "__main__":
    main()
