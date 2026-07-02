#!/usr/bin/env bash
# bench.sh — single entrypoint for the REST-MCP vs GraphQL-MCP token benchmark.
#
#   ./bench.sh                 # = all: setup -> precheck -> capture -> run -> parse
#   ./bench.sh setup           # install/verify deps, fetch SDL, render configs
#   ./bench.sh precheck        # STEP-1 GATE: confirm proxy logs cache_*_input_tokens
#   ./bench.sh capture         # record real MCP tool surfaces + response shapes
#   ./bench.sh run             # run the matrix (A1,A2,B[,C]) x tasks x REPS
#   ./bench.sh parse           # build results/summary.md + CSVs
#   ./bench.sh clean           # remove runs/ results/ capture/*.json
#
# Config comes from .env (see .env.example). ANTHROPIC_API_KEY is required.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_ROOT
cd "$PROJECT_ROOT"

# --- config ---
# Load .env as DEFAULTS: a value already in the environment wins, so
# `MAX_TURNS=50 ./bench.sh run` overrides the file. Inline `# comments` and
# surrounding whitespace are stripped; values may contain '='.
if [ -f "$PROJECT_ROOT/.env" ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    _line=${_line%$'\r'}
    _trim=${_line#"${_line%%[![:space:]]*}"}      # left-trimmed copy
    case "$_trim" in ''|'#'*) continue ;; esac     # skip blanks / comment lines
    [ "${_line%%=*}" = "$_line" ] && continue       # require '='
    _k=${_line%%=*}; _k=${_k//[[:space:]]/}         # key (no spaces)
    _v=${_line#*=}
    _v=${_v%%" #"*}                                 # drop ' # inline comment'
    _v="${_v#"${_v%%[![:space:]]*}"}"               # left-trim value
    _v="${_v%"${_v##*[![:space:]]}"}"               # right-trim value
    [ -z "${!_k+x}" ] && export "$_k=$_v"
  done < "$PROJECT_ROOT/.env"
fi
: "${REPO:=graphql/graphql-js}"
: "${WINDOW_START:=2026-03-01}"
: "${WINDOW_END:=2026-05-31}"
: "${FILE_PATH:=src/execution/execute.ts}"
: "${MODEL:=}"            # blank => recipe default claude-sonnet-4-6
: "${REPS:=3}"
: "${PORT:=8080}"
: "${MAX_TURNS:=50}"
: "${ENABLE_ROVER:=0}"
: "${CONDITIONS:=}"   # comma-separated subset, e.g. A1,B2 — empty means all
: "${TASKS:=}"        # comma-separated subset, e.g. T2    — empty means all
# Download version for the apollo-mcp-server binary. Deliberately NOT named with
# the APOLLO_MCP_ prefix: the Apollo MCP server reads every APOLLO_MCP_* env var
# as a config override, so APOLLO_MCP_VERSION parses as the unknown config key
# "version" and the server refuses to start. Accept the old name from an existing
# .env for back-compat, then unset it so it can't leak into the server process.
: "${APOLLO_BIN_VERSION:=${APOLLO_MCP_VERSION:-v1.14.0}}"
unset APOLLO_MCP_VERSION
export REPO WINDOW_START WINDOW_END FILE_PATH MODEL REPS PORT MAX_TURNS ENABLE_ROVER APOLLO_BIN_VERSION CONDITIONS TASKS

# shellcheck source=lib/setup.sh
. "$PROJECT_ROOT/lib/setup.sh"

# ---------------------------------------------------------------------------
do_precheck() {
  echo "== precheck (Step-1 gate: cache_*_input_tokens present in proxy log) =="
  ensure_prereqs_min
  local d="$PROJECT_ROOT/runs/_precheck"; mkdir -p "$d"
  cat > "$d/recipe.yaml" <<EOF
version: "1.0.0"
title: "precheck"
description: "minimal single-call probe to verify the proxy logs cache token fields"
prompt: |
  Reply with exactly one word: READY
settings:
  goose_provider: anthropic
  goose_model: ${MODEL:-claude-sonnet-4-6}
  temperature: 0
  max_turns: 2
EOF

  # clear stale Goose logs AND the per-run proxy log (proxy appends)
  local gld="${GOOSE_LOG_DIR:-$HOME/.local/state/goose/logs}"
  rm -f "$gld"/llm_request*.jsonl 2>/dev/null || true
  rm -f "$d/proxy.jsonl" "$d"/goose_*.jsonl 2>/dev/null || true

  PROXY_LOG="$d/proxy.jsonl" RUN_LABEL=precheck PORT="$PORT" \
    uv run "$PROJECT_ROOT/proxy/anthropic_logging_proxy.py" >"$d/proxy_server.log" 2>&1 &
  local proxy_pid=$!
  trap 'kill '"$proxy_pid"' 2>/dev/null || true' RETURN

  # wait for health
  local ok=0 i
  for i in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$PORT/__health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 0.4
  done
  [ "$ok" = 1 ] || { echo "ERROR: proxy did not become healthy"; return 1; }

  ANTHROPIC_HOST="http://127.0.0.1:$PORT" \
    goose run --recipe "$d/recipe.yaml" --no-session --max-turns 2 >"$d/stdout.txt" 2>"$d/stderr.txt" || true

  # snapshot goose logs for the cross-check field-name confirmation
  cp "$gld"/llm_request*.jsonl "$d/" 2>/dev/null || true
  for f in "$d"/llm_request*.jsonl; do [ -e "$f" ] && mv "$f" "$d/goose_$(basename "$f")"; done 2>/dev/null || true

  python3 - "$d" <<'PY'
import json, sys, glob, os
d = sys.argv[1]
msgs = []
for line in open(os.path.join(d, "proxy.jsonl")):
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("is_messages"):
        msgs.append(r)
if not msgs:
    print("FAIL: proxy logged no /v1/messages calls — is ANTHROPIC_HOST wired and the key valid?")
    sys.exit(2)
have_read = any(m.get("cache_read_input_tokens") is not None for m in msgs)
have_create = any(m.get("cache_creation_input_tokens") is not None for m in msgs)
m0 = msgs[0]
print(f"proxy: {len(msgs)} call(s); first usage = "
      f"input={m0.get('input_tokens')} output={m0.get('output_tokens')} "
      f"cache_read_input_tokens={m0.get('cache_read_input_tokens')} "
      f"cache_creation_input_tokens={m0.get('cache_creation_input_tokens')}")
# Goose cross-check: confirm the renamed field is present somewhere
gtext = "".join(open(p).read() for p in glob.glob(os.path.join(d, "goose_*.jsonl")))
note = "cache_read_tokens" if "cache_read_tokens" in gtext else (
       "cache_read_input_tokens" if "cache_read_input_tokens" in gtext else "NONE")
print(f"goose JSONL cache field seen: {note}")
if have_read and have_create:
    print("PASS: proxy preserves cache_read_input_tokens AND cache_creation_input_tokens.")
    print("NOTE: proxy is authoritative (no 10-file rotation cap). Goose's llm_request log "
          "carries the raw field too, per the cross-check above.")
    sys.exit(0)
print("FAIL: cache token fields absent from the raw usage object — FLAG before proceeding.")
sys.exit(3)
PY
  local rc=$?
  echo "== precheck $( [ $rc -eq 0 ] && echo PASS || echo FAIL ) =="
  return $rc
}

# ---------------------------------------------------------------------------
do_capture() {
  echo "== capture (real MCP tool surfaces + response shapes) =="
  ensure_prereqs_min
  ensure_docker   # A1/A2 capture the GitHub MCP server via Docker
  [ -f "$PROJECT_ROOT/config/apollo-mcp.github.local.yaml" ] || { echo "ERROR: run setup first (missing rendered Apollo config)"; return 1; }
  mkdir -p "$PROJECT_ROOT/capture"
  local owner="${REPO%%/*}" name="${REPO##*/}"

  # Build representative REST tool-call specs.
  local rest_calls
  rest_calls=$(REPO="$REPO" OWNER="$owner" NAME="$name" FILE_PATH="$FILE_PATH" python3 - <<'PY'
import json, os
o, n, fp, repo = os.environ["OWNER"], os.environ["NAME"], os.environ["FILE_PATH"], os.environ["REPO"]
print(json.dumps([
  {"name": "list_pull_requests", "arguments": {"owner": o, "repo": n, "state": "closed", "perPage": 5}},
  {"name": "search_issues", "arguments": {"q": f"repo:{repo} is:issue is:open performance", "perPage": 5}},
  {"name": "list_commits", "arguments": {"owner": o, "repo": n, "path": fp, "perPage": 5}},
]))
PY
)
  # A1 (all = the server's default) and A2 (minimal) — same calls, different --toolsets.
  python3 "$PROJECT_ROOT/capture/capture_mcp.py" \
    --label A1 --out "$PROJECT_ROOT/capture/A1.json" --calls "$rest_calls" \
    -- docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server \
       ./github-mcp-server stdio --read-only --toolsets all || true
  python3 "$PROJECT_ROOT/capture/capture_mcp.py" \
    --label A2 --out "$PROJECT_ROOT/capture/A2.json" --calls "$rest_calls" \
    -- docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server \
       ./github-mcp-server stdio --read-only --toolsets repos,issues,pull_requests || true

  # B (GraphQL) — execute representative queries via the Apollo MCP server.
  local gql_calls
  gql_calls=$(REPO="$REPO" OWNER="$owner" NAME="$name" FILE_PATH="$FILE_PATH" python3 - <<'PY'
import json, os
o, n, fp = os.environ["OWNER"], os.environ["NAME"], os.environ["FILE_PATH"]
commits = ('{ repository(owner:"%s", name:"%s"){ defaultBranchRef{ target{ ... on Commit { '
           'history(first:5, path:"%s"){ nodes{ oid messageHeadline committedDate '
           'author{ user{ login } } } } } } } } }') % (o, n, fp)
prs = ('{ repository(owner:"%s", name:"%s"){ pullRequests(first:5, states:MERGED){ nodes{ '
       'number title author{ login } commits(last:1){ nodes{ commit{ '
       'statusCheckRollup{ state } } } } } } } }') % (o, n)
print(json.dumps([
  {"name": "execute", "arguments": {"query": commits}},
  {"name": "execute", "arguments": {"query": prs}},
]))
PY
)
  python3 "$PROJECT_ROOT/capture/capture_mcp.py" \
    --label B --out "$PROJECT_ROOT/capture/B.json" --calls "$gql_calls" \
    -- "$PROJECT_ROOT/bin/apollo-mcp-server" "$PROJECT_ROOT/config/apollo-mcp.github.local.yaml" || true

  # Summarize into capture/SUMMARY.md (referenced by NOTES.md).
  python3 - "$PROJECT_ROOT/capture" <<'PY'
import json, glob, os, sys
d = sys.argv[1]
out = ["# Captured MCP tool surfaces & response shapes\n",
       "Generated by `./bench.sh capture`. Used to ground claims in actual MCP output.\n",
       "| Condition | # tools | tools/list bytes | representative calls (name: result bytes) |",
       "|---|---|---|---|"]
for f in sorted(glob.glob(os.path.join(d, "*.json"))):
    r = json.load(open(f))
    calls = "; ".join(f"{c.get('name')}: {c.get('result_bytes','err')}" for c in r.get("calls", []))
    out.append(f"| {r.get('label')} | {r.get('n_tools','?')} | {r.get('tools_list_bytes','?')} | {calls} |")
out.append("\nFull payloads (including result previews) are in `capture/<label>.json`.\n")
out.append("Key point: a large `# tools` / `tools/list bytes` is the tool-schema overhead the "
           "REST condition pays on every cached prefix; GraphQL exposes only 4 tools.\n")
open(os.path.join(d, "SUMMARY.md"), "w").write("\n".join(out) + "\n")
print("wrote", os.path.join(d, "SUMMARY.md"))
PY
  echo "== capture done (see capture/SUMMARY.md) =="
}

# ---------------------------------------------------------------------------
do_run() {
  echo "== run matrix =="
  ensure_prereqs_min
  # GitHub MCP (A1/A2) needs Docker; skip the check only if the filter excludes them.
  if [ -z "${CONDITIONS:-}" ] || [[ ",$CONDITIONS," == *",A1,"* ]] || [[ ",$CONDITIONS," == *",A2,"* ]]; then
    ensure_docker
  fi
  uv run "$PROJECT_ROOT/run_benchmark.py"
}

do_parse() {
  echo "== parse =="
  python3 "$PROJECT_ROOT/parse_logs.py"
}

do_clean() {
  rm -rf "$PROJECT_ROOT/runs" "$PROJECT_ROOT/results"
  rm -f "$PROJECT_ROOT/capture"/*.json "$PROJECT_ROOT/capture/SUMMARY.md"
  echo "cleaned runs/, results/, capture/*.json"
}

case "${1:-all}" in
  setup)    do_setup ;;
  precheck) do_precheck ;;
  capture)  do_capture ;;
  run)      do_run ;;
  parse)    do_parse ;;
  clean)    do_clean ;;
  all)      do_setup && do_precheck && do_capture && do_run && do_parse ;;
  *) echo "usage: ./bench.sh [setup|precheck|capture|run|parse|clean|all]"; exit 2 ;;
esac
