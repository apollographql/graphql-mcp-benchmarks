# lib/setup.sh — idempotent setup, sourced by bench.sh. Defines functions only.
#
# do_setup(): install/verify Goose, fetch the Apollo MCP binary, pull the GitHub
# MCP Docker image, download GitHub's GraphQL SDL via rover, render the Apollo
# config, and warm the proxy's uv deps. Safe to re-run; skips what's present.

_need() {  # _need <cmd> <install hint>
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found. $2"; return 1; }
}

ensure_token() {
  if [ -z "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]; then
    GITHUB_PERSONAL_ACCESS_TOKEN="$(gh auth token 2>/dev/null || true)"
  fi
  [ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ] || {
    echo "ERROR: no GitHub token. Run: gh auth login"; return 1; }
  export GITHUB_PERSONAL_ACCESS_TOKEN
  export GITHUB_TOKEN="$GITHUB_PERSONAL_ACCESS_TOKEN"   # Apollo config reads ${env.GITHUB_TOKEN}
}

ensure_docker() {
  command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found (needed for conditions A1/A2)"; return 1; }
  docker info >/dev/null 2>&1 || {
    echo "ERROR: the Docker daemon is not running — start Docker Desktop and retry."
    echo "       (Conditions A1/A2 run GitHub's MCP server as a Docker container.)"
    return 1; }
}

ensure_prereqs_min() {
  [ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ERROR: ANTHROPIC_API_KEY is not set (put it in .env)"; return 1; }
  _need uv "Install: https://docs.astral.sh/uv/" || return 1
  _need python3 "Install Python 3.10+" || return 1
  command -v goose >/dev/null 2>&1 || { echo "ERROR: goose not installed — run './bench.sh setup' first"; return 1; }
  export PATH="$PROJECT_ROOT/bin:$PATH"   # apollo-mcp-server lives in ./bin
  ensure_token || return 1
}

do_setup() {
  echo "== setup =="
  [ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ERROR: ANTHROPIC_API_KEY is not set (put it in .env)"; return 1; }
  _need docker "Install Docker Desktop" || return 1
  ensure_docker || return 1
  _need gh "Install GitHub CLI and run: gh auth login" || return 1
  _need rover "Install: https://www.apollographql.com/docs/rover/getting-started" || return 1
  # Conditions B2 and M-G1 shell out to `rover schema search` / `rover schema describe`,
  # which were added in rover v0.38/v0.40. An older rover satisfies `command -v rover`
  # but makes them fail at run time, so probe the capability here instead of at first use.
  # (Phase 2's other rover uses — `supergraph compose` and `dev` — are much older and
  # are not covered by this probe.)
  if ! rover schema --help >/dev/null 2>&1; then
    echo "WARNING: this rover ($(rover --version 2>/dev/null | head -1)) has no 'rover schema' subcommand."
    echo "         Conditions B2 (servers/rover_schema_mcp.py) and M-G1"
    echo "         (servers/supergraph_mcp.py) require 'rover schema search'/'describe'"
    echo "         (rover >= v0.40) and will fail."
    echo "         Upgrade rover, drop those conditions, or place a newer rover in ./bin"
    echo "         (already first on PATH, same as apollo-mcp-server)."
  fi
  _need uv "Install: https://docs.astral.sh/uv/" || return 1
  _need python3 "Install Python 3.10+" || return 1
  ensure_token || return 1

  # --- Goose CLI ---
  # Every other version in this study is pinned; Goose was not, and its version was
  # recorded in no meta.json. That is the one component the largest cost caveat
  # blames ("this is the client's breakpoint placement"), so an unpinned, unrecorded
  # version made the caveat unfalsifiable by anyone including us. The published
  # matrix ran on GOOSE_VERSION below. `brew install` and the `stable` channel both
  # move, so this warns loudly rather than failing: pinning an installed Goose is
  # not something setup can do for you.
  : "${GOOSE_VERSION:=1.37.0}"     # the version the published matrix ran on
  if ! command -v goose >/dev/null 2>&1; then
    echo "Installing Goose..."
    if command -v brew >/dev/null 2>&1; then
      brew install block-goose-cli || true
    fi
    if ! command -v goose >/dev/null 2>&1; then
      CONFIGURE=false bash -c "$(curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh)" || true
    fi
  fi
  command -v goose >/dev/null 2>&1 || { echo "ERROR: Goose install failed; install it manually (https://goose-docs.ai/docs/getting-started/installation)"; return 1; }
  local goose_have; goose_have="$(goose --version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  echo "goose: $(command -v goose) (${goose_have:-unknown})"
  if [ -n "$goose_have" ] && [ "$goose_have" != "$GOOSE_VERSION" ]; then
    echo "WARNING: goose ${goose_have} != the published matrix's ${GOOSE_VERSION}."
    echo "         Harness behaviour — turn handling, cache_control placement, tool"
    echo "         serialization — is not held constant across Goose versions, and"
    echo "         NOTES.md 51/69 turn on exactly that. Results are still recorded"
    echo "         (run_benchmark.py writes goose_version into every meta.json), but"
    echo "         they are not directly comparable to the published numbers."
  fi

  # Minimal Goose config so headless runs pick up provider/model (env still overrides).
  local gcfg="$HOME/.config/goose/config.yaml"
  if [ ! -f "$gcfg" ]; then
    mkdir -p "$(dirname "$gcfg")"
    printf 'GOOSE_PROVIDER: anthropic\nGOOSE_MODEL: %s\n' "${MODEL:-claude-sonnet-4-6}" > "$gcfg"
    echo "wrote $gcfg"
  fi

  # --- GitHub MCP Server image ---
  echo "Pulling ghcr.io/github/github-mcp-server ..."
  docker pull ghcr.io/github/github-mcp-server >/dev/null

  # --- Apollo MCP Server binary (Apple Silicon) ---
  mkdir -p "$PROJECT_ROOT/bin"
  if [ ! -x "$PROJECT_ROOT/bin/apollo-mcp-server" ]; then
    local ver="${APOLLO_BIN_VERSION:-v1.14.0}"
    # aarch64-apple-darwin only. Reproducing this repo needs an Apple Silicon Mac
    # (or a hand-placed bin/apollo-mcp-server for your platform) — stated here
    # because the README's "one command" reads as portable and is not.
    case "$(uname -s)/$(uname -m)" in
      Darwin/arm64) ;;
      *) echo "ERROR: setup only downloads the aarch64-apple-darwin build of"
         echo "       apollo-mcp-server. On $(uname -s)/$(uname -m), fetch the matching"
         echo "       release from github.com/apollographql/apollo-mcp-server and place"
         echo "       it at bin/apollo-mcp-server, then re-run setup."
         return 1 ;;
    esac
    local tarball="apollo-mcp-server-${ver}-aarch64-apple-darwin.tar.gz"
    local url="https://github.com/apollographql/apollo-mcp-server/releases/download/${ver}/${tarball}"
    echo "Downloading Apollo MCP Server ${ver} ..."
    local tmp; tmp="$(mktemp -d)"
    curl -fsSL "$url" -o "$tmp/$tarball" || { echo "ERROR: download failed: $url"; return 1; }
    tar -xzf "$tmp/$tarball" -C "$tmp"
    local found; found="$(find "$tmp" -type f -name apollo-mcp-server | head -1)"
    [ -n "$found" ] || { echo "ERROR: apollo-mcp-server binary not found in tarball"; return 1; }
    cp "$found" "$PROJECT_ROOT/bin/apollo-mcp-server"
    chmod +x "$PROJECT_ROOT/bin/apollo-mcp-server"
    rm -rf "$tmp"
  fi
  echo "apollo-mcp-server: $("$PROJECT_ROOT/bin/apollo-mcp-server" --version 2>/dev/null || echo present)"

  # --- GitHub GraphQL SDL (Apollo MCP has no live introspection) ---
  if [ ! -s "$PROJECT_ROOT/config/github.graphql" ]; then
    echo "Downloading GitHub GraphQL SDL via rover ..."
    rover graph introspect https://api.github.com/graphql \
      --header "Authorization: Bearer ${GITHUB_TOKEN}" \
      > "$PROJECT_ROOT/config/github.graphql" || { echo "ERROR: rover introspect failed"; return 1; }
  fi
  [ -s "$PROJECT_ROOT/config/github.graphql" ] || { echo "ERROR: empty SDL"; return 1; }
  echo "SDL: $(wc -l < "$PROJECT_ROOT/config/github.graphql") lines"

  # --- Render Apollo config with absolute SDL path ---
  SDL_ABS="$PROJECT_ROOT/config/github.graphql" \
  python3 - "$PROJECT_ROOT/config/apollo-mcp.github.yaml" "$PROJECT_ROOT/config/apollo-mcp.github.local.yaml" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
open(dst, "w").write(open(src).read().replace("@@SDL_PATH@@", os.environ["SDL_ABS"]))
print("wrote", dst)
PY

  # --- Render the phase-2 Apollo config (M-G2) with absolute paths ---
  # Rendered unconditionally: it costs nothing, and the phase-2 conditions fail
  # confusingly if the file is missing. The supergraph itself is built by
  # `cd services && pnpm build`, which is a separate (node + rover) prerequisite.
  SUPERGRAPH_ABS="$PROJECT_ROOT/services/generated/supergraph.graphql" \
  OPERATIONS_ABS="$PROJECT_ROOT/services/operations" \
  python3 - "$PROJECT_ROOT/config/apollo-mcp.phase2.yaml" "$PROJECT_ROOT/config/apollo-mcp.phase2.local.yaml" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()
text = text.replace("@@SUPERGRAPH_PATH@@", os.environ["SUPERGRAPH_ABS"])
text = text.replace("@@OPERATIONS_DIR@@", os.environ["OPERATIONS_ABS"])
open(dst, "w").write(text)
print("wrote", dst)
PY

  # --- Warm uv deps for the proxy ---
  uv run "$PROJECT_ROOT/proxy/anthropic_logging_proxy.py" --selfcheck

  echo "== setup OK =="
}
