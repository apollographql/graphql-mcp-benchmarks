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
  _need uv "Install: https://docs.astral.sh/uv/" || return 1
  _need python3 "Install Python 3.10+" || return 1
  ensure_token || return 1

  # --- Goose CLI ---
  if ! command -v goose >/dev/null 2>&1; then
    echo "Installing Goose..."
    if command -v brew >/dev/null 2>&1; then
      brew install block-goose-cli || true
    fi
    if ! command -v goose >/dev/null 2>&1; then
      CONFIGURE=false bash -c "$(curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh)" || true
    fi
  fi
  command -v goose >/dev/null 2>&1 || { echo "ERROR: Goose install failed; install it manually (https://block.github.io/goose/)"; return 1; }
  echo "goose: $(command -v goose)"

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

  # --- Warm uv deps for the proxy ---
  uv run "$PROJECT_ROOT/proxy/anthropic_logging_proxy.py" --selfcheck

  echo "== setup OK =="
}
