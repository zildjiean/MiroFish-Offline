# MiroFish-Offline — install notes (host: mirofish / 10.16.16.12)

Installed 2026-09-09. This deployment deviates from the upstream README in several
ways that are **not optional** on this host. Read this before reinstalling or upgrading.

## 1. The network blocks most package registries (FortiGuard Secure DNS)

Blocked domains resolve to the sinkhole `208.91.112.55`, which serves a
self-signed "Fortiguard SDNS Blocked Page" cert, so TLS fails rather than 404s.

| Blocked | Used instead |
|---|---|
| `registry-1.docker.io` | `mirror.gcr.io` — see `/etc/docker/daemon.json` |
| `pypi.org` | `pypi.tuna.tsinghua.edu.cn` — see `~/.pip/pip.conf` |
| `registry.npmjs.org` | `registry.npmmirror.com` — `npm config set registry` |
| `ghcr.io` | avoided entirely (see §2) |
| `download.docker.com`, `ollama.com` | Ubuntu apt / not needed |

Reachable: `github.com`, `archive.ubuntu.com`, `deb.nodesource.com`,
`ppa.launchpadcontent.net`, `huggingface.co`, `files.pythonhosted.org`.

**The LLM gateway `ai-gateway.suphan.dev` is in a blocked FortiGuard category.**
It currently resolves to real Cloudflare IPv6 and works, but this has already
flipped once during install. If the app starts failing with connection errors,
check first:

    getent hosts ai-gateway.suphan.dev     # 208.91.112.55 or 2620:101:9000::/44 == blocked again

Workaround if blocked: pin a Cloudflare IP in `/etc/hosts`. **Proper fix: ask IT to
allowlist the domain in FortiGuard** — pinned IPs go stale when Cloudflare rotates.

## 2. Docker is used for Neo4j only, not for the app

The repo `Dockerfile` does `COPY --from=ghcr.io/astral-sh/uv:0.9.26` and runs
`npm ci` / `uv sync` at build time — all three registries are blocked, so it
cannot build here. Backend and frontend run natively instead.

## 3. Python 3.11 is required (README says "3.11+", which is wrong)

`camel-oasis==0.2.5` requires `>=3.10,<3.12`. Ubuntu 24.04 ships 3.12, which
fails dependency resolution. Python 3.11.15 installed from the deadsnakes PPA;
venv at `~/MiroFish-Offline/.venv`.

`npm run backend` was repointed from `uv run` to that venv.

## 4. Ollama is not installed — embeddings run locally, LLM runs on the gateway

The gateway serves **chat models only** (`qwen3.8-27b`, `qwen3.8-flash-next`);
it has no `/v1/embeddings` deployment. Embeddings therefore run in-process via
`sentence-transformers` (already a `camel-oasis` dependency, so no extra install).

### Code changes (originals kept as `*.orig`)

- `backend/app/storage/embedding_service.py` — rewritten. Upstream only spoke
  Ollama's native `POST /api/embed`. Now supports `EMBEDDING_API_STYLE`:
  `local` (in-process model, current setting), `openai` (`POST /v1/embeddings`
  for a gateway that has one), `ollama` (original behaviour).
- `backend/app/config.py` — added `EMBEDDING_API_STYLE`, `EMBEDDING_API_KEY`,
  `EMBEDDING_DIM`, `EMBEDDING_TEXT_PREFIX`, `EMBEDDING_TORCH_THREADS`.
- `backend/app/storage/neo4j_schema.py` — vector dimension was hardcoded `768`
  in both indexes; now reads `Config.EMBEDDING_DIM`.
- `frontend/vite.config.js` — `open: false` (headless server).

### Embedding model choice

`sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (768-d, Thai+English).

Benchmarked against `intfloat/multilingual-e5-base` on Thai/English pairs.
E5 separated related from unrelated text by only **+0.095** (everything scored
0.70–0.89, which breaks threshold-based ranking); mpnet separated by **+0.796**.
mpnet also needs no `query: ` prefix. 768-d keeps the original index dimension.

**Changing the embedding model means changing `EMBEDDING_DIM`, dropping both
vector indexes, and rebuilding every graph** — the dimension is baked into the index.

## 5. Services

    systemctl status mirofish-backend mirofish-frontend   # both enabled at boot
    docker ps                                             # mirofish-neo4j, restart=unless-stopped
    tail -f ~/MiroFish-Offline/logs/{backend,frontend}.log

| Port | Service |
|---|---|
| 3000 | Frontend (Vite) — main UI |
| 5001 | Backend Flask API |
| 7474 / 7687 | Neo4j browser / Bolt |

Neo4j heap capped at 1g + 512m pagecache to fit 7.8 GB RAM.

## 6. Known caveats

- **The frontend runs the Vite dev server**, per the repo's own `npm run dev` flow.
  For a hardened deploy, `npm run build` and serve `frontend/dist` behind a real
  web server, with `/api` proxied to :5001.
- The backend is Flask's dev server. Fine for internal use; use a WSGI server
  (gunicorn/waitress) if this is exposed more widely.
- **No firewall is active** (`ufw` inactive) — all four ports are on `0.0.0.0`.
- The venv is ~6 GB because `torch` pulls CUDA wheels that are useless on this
  CPU-only host. Harmless; a CPU-only torch build would reclaim ~4 GB.
- The gateway returned a transient `429 "No deployments available"` during
  testing that cleared on retry — the app has retry logic, but expect occasional
  slow calls under load.

## 7. Upstream bug: frontend called `localhost:5001` from the browser

`frontend/src/api/index.js` shipped with:

    baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'

Because that is an **absolute** URL, every API call resolved against the *browser's*
machine, not the server. Opening the UI from any other computer produced a stuck
"Uploading and analyzing docs..." spinner and an `Error` badge, while the backend log
stayed completely silent — no request ever arrived. It also bypassed the `/api` proxy
in `vite.config.js` entirely, so the proxy was never actually exercised.

Fixed by defaulting to a **relative** base URL (original kept as `index.js.orig`):

    baseURL: import.meta.env.VITE_API_BASE_URL || ''

All call sites already use `/api/...` paths, so requests now go to the same origin the
page was served from and Vite forwards them to :5001. This works from any host with no
per-machine config and no CORS involvement.

Note `VITE_API_BASE_URL=''` in a `.env` would **not** have fixed it — an empty string is
falsy in JS, so `||` still fell through to the localhost default. The code had to change.

If you later switch to a production build (`npm run build`), the Vite proxy no longer
exists — the web server serving `frontend/dist` must proxy `/api` to :5001 itself.

## 8. Observed performance

Ontology generation on a ~360-character Thai document took **~92 s** (11:16:11 → 11:17:43),
essentially all of it waiting on the gateway LLM. Graph build of a 622-character English
document took ~36 s. Embedding is not the bottleneck — a batch of 4 texts encodes in
0.14 s locally. Expect simulation runs with hundreds of agents to be gateway-bound.

## 9. LLM provider switched to OpenCode Zen "Go" (2026-09-09)

The previous gateway (`ai-gateway.suphan.dev`, LiteLLM) was abandoned: its vLLM origin
could not answer a persona-sized prompt inside Cloudflare's 120 s proxy read timeout.
**75 of 77 personas silently fell back to rule-based templates** while the UI still
reported "Successfully generated persona". Streaming did not help — no first token
arrived within 120 s either, so the origin was stalled rather than merely slow.

Current: `https://opencode.ai/zen/go/v1`, model `mimo-v2.5`.

### Three things had to be solved

**a) `opencode.ai` is DNS-blocked by FortiGuard here** (see §1). Cloudflare IPs are
pinned in `/etc/hosts`; AAAA records currently resolve correctly on their own. If the
app starts failing, check `getent hosts opencode.ai` first. Proper fix: IT allowlist.

**b) Every request needs an `x-opencode-session` header**, or the API returns
`400 MissingSessionID`. MiroFish cannot set this on the clients CAMEL-AI/OASIS builds
internally, so headers are injected globally by:

    .venv/lib/python3.11/site-packages/mirofish_llm_headers.py
    .venv/lib/python3.11/site-packages/zz_mirofish_llm_headers.pth

The `.pth` runs at interpreter startup for **every** process in the venv, including the
OASIS simulation subprocess. Two non-obvious details:

- A `sitecustomize.py` does **not** work — Debian ships `/usr/lib/python3.11/sitecustomize.py`,
  which appears first on `sys.path` and shadows one placed in site-packages.
- Headers are read when a client is **constructed**, not at import time. The `.pth` runs
  before the app calls python-dotenv, so reading the environment at import time saw
  nothing. This was an actual bug during setup, fixed by deferring the lookup.

Configured via `.env` (`OPENCODE_SESSION_ID`, `LLM_USER_AGENT`, or `LLM_EXTRA_HEADERS`
as a JSON object). The patch is a no-op when those are unset, so it is safe to leave in
place if the provider changes again. Debug with `MIROFISH_HEADER_DEBUG=1`.

**c) Model choice.** Measured on this host with a persona-sized prompt:

| model | persona | tok/s | JSON | tool calling |
|---|---|---|---|---|
| **mimo-v2.5** | **11.7 s** | 39.7 | **valid** | **works** |
| qwen3.8-flash | 52.1 s | 66.4 | valid | works |
| glm-5.3-flash | 19.7 s | 60.9 | truncated | works |
| minimax-m3 | 16.3 s | 45.9 | **invalid** | works |

`mimo-v2.5` wins on wall-clock despite lower tok/s because it is far less verbose
(463 output tokens vs 3,456), and it has the largest request allowance of the usable
models. Tool calling matters: ReportAgent and OASIS agents depend on it.

Avoid `muse-spark-1.*-contributor`: returns `403 DataPolicyError` until you opt in, and
opting in means **the documents you upload are used to train the model**. Also
region-limited. `deepseek-v4-*` returns `RegionError` (China-hosted, opt-in required).

### Verified after the switch

Ontology generation 21 s (was 92 s). Graph build fine. Persona generation:
**5/5 by LLM, 0 rule-based fallbacks**, Thai entity names preserved, personas contain
genuine per-entity detail rather than templates.

### ⚠ Acceptable-use risk

OpenCode's own docs say Go "is designed for OpenCode and other coding agents that
produce similar types of requests. Traffic is monitored for abuse that degrades the
experience for other users." **MiroFish is not a coding agent** — a simulation fires
hundreds to thousands of persona and agent-action calls in bursts. That traffic shape
does not resemble a coding agent's, and the subscription could be throttled or revoked.
This is a judgement call for the account owner, not a technical blocker.

Quota is dollar-based: **$12 per 5 h, $30 per week, $60 per month**. For `mimo-v2.5`
OpenCode estimates ~30,100 requests per 5 h, so persona generation (1 request per
entity) is cheap; long multi-round simulations are what will consume it.
