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

## 10. Memory: OASIS state growth is the real limit

The host was raised from 8 GB to 16 GB during testing because simulations were being
killed by the OOM killer:

    Out of memory: Killed process (python) anon-rss:7643096kB
    task_memcg=/system.slice/mirofish-backend.service

That was at **round 17 of 24** with 35 agents. OASIS keeps the whole simulation state
in memory — every post, follow edge, and each agent's accumulated memory of what it has
seen — and the growth is **not linear**:

| simulated round | OASIS RSS |
|---|---|
| 17 | 5.1 GB |
| 20 | 8.0 GB |

Roughly 1 GB per round at that point, accelerating, because each round every awake
agent reads a larger backlog. Stopping the run returns all of it immediately
(8.8 GB → 1.9 GB used), confirming it is in-process state rather than a leak elsewhere.

Practical guidance for 35 agents: 8 GB cannot finish 24 rounds; 16 GB handled 20+
rounds with room to spare but would likely not reach 72. Fewer agents, or fewer rounds,
matters more than more RAM.

## 11. How many rounds are actually worth running

`total_simulation_hours` is generated by the LLM in `simulation_config_generator.py`,
not fixed — one run produced 96 hours, another 72. The number in the UI before a config
exists is just the default. Measured action counts per round from a real run:

| rounds | wall clock hour | actions/round | agents awake |
|---|---|---|---|
| 0–5 | 00:00–05:00 (off-peak) | 4–16 | 1–8 |
| 9–17 | 09:00–17:00 | 18–32 | 10–23 |
| 19–20 | 19:00–20:00 (peak) | 31–33 | 20 |
| 24–29 | 00:00–05:00, day 2 | 2–8 | 1–2 |
| 33–35 | 09:00–11:00, day 2 | 1–14 | 1–13 |

Two things follow. Night rounds cost the same time and memory as any other but produce
almost nothing — 6 of every 24 rounds are near-empty. And activity drops by more than
half on day two, once agents have said what they have to say.

**~24 rounds is the sweet spot**: one full daily cycle through the peak window. To cover
more simulated days, raise `minutes_per_round` rather than the round count — that skips
the dead night hours instead of simulating them.

## 12. Report generation verified in Thai

`ReportAgent` was exercised end to end against a stopped simulation with 398 actions:

- completed in 341 s, no errors
- 9,427 characters, 76 Thai lines, **0 English lines** outside quotes and headings
- outline and all three section headings in Thai
- tool calling worked through the gateway — the report cites facts retrieved from Neo4j
  (`"LeakRadar shows a histogram of 77 credentials connected to ops.moc.go.th"`)
- quoted evidence keeps its original language, as `report_rule` instructs, so the reader
  sees what agents actually said rather than a translation

This also confirms the `.format()` placeholder rework in `report_agent.py` behaves in
production, not just in isolation.

## 13. Upstream bug: report chat never rendered a reply

`ReportAgent.chat()` returns `{response, sources, tool_calls}`, and the API nests that
under `data.response`. `Step5Interaction.vue` assigned `res.data.response` straight into
the chat bubble as message content, so it received an object where a string was expected
and the UI sat on its typing indicator forever. The backend was answering correctly the
whole time — HTTP 200 in 13 s.

Fixed with `extractAgentText()`, which accepts either shape, so it keeps working if the
backend response is ever flattened. Original kept as `Step5Interaction.vue.orig`.

The same `agentResult.response || agentResult.answer` pattern appears around lines 751,
755, 840 and 846 for the "chat with any individual" flow. Untested — if that view hangs
the same way, it needs the same treatment.

## 14. Provider moved to OpenRouter

`.env` now points at `https://openrouter.ai/api/v1` with
`deepseek/deepseek-v4-flash-0731`. Three things this fixed or changed:

**Thai output is clean.** The CJK contamination that forced the guard in §"Guarding
against script mixing" does not occur with this model — 0% in personas (guard fired
zero times against 35, versus 65 times with `mimo-v2.5`) and 0% across agent posts
sampled from a live run. The guard stays as a safety net; it costs nothing when idle.

**`openrouter.ai` is not sinkholed by FortiGuard**, so it needs no `/etc/hosts` pin.
That removes the intermittent DNS failure mode that broke a `git push` and, earlier,
the LLM gateway itself.

**Rounds run roughly 30× faster** — about 4 s per round against 122 s with
`mimo-v2.5`, mostly because this model does not emit long reasoning preambles.

### The switch has one trap

`OPENAI_API_KEY` and `OPENAI_API_BASE_URL` are read by CAMEL-AI/OASIS, not by
MiroFish's own code. Changing only the `LLM_*` variables sends personas and reports to
the new provider while every simulation agent keeps calling the old one — two bills,
and very confusing to debug. Both pairs must move together.

## 15. Two defects found while testing on OpenRouter

**Speed made the memory ceiling worse, not better.** With `mimo-v2.5` a 24-round run
died at round 20 on 16 GB; with the faster model it died at **round 9 of 24** on the
same 16 GB. Agents produce state faster than it can be reclaimed, and OASIS loads its
own extra model (`Twitter/twhin-bert-base`) on top. Treat rounds × agents as the budget,
not wall-clock time — a faster provider does not buy longer runs.

**Embedding model load races the thread pool.** With `parallel_profile_count=5`, several
workers hit `EmbeddingService` before the sentence-transformers model has finished
loading and get:

    Knowledge graph search failed (<entity>): Cannot copy out of meta tensor; no data!

Four personas per run are affected. They are still generated, but **without knowledge
graph enrichment**, so they are blander than the rest — and the failure is only a
warning, so nothing surfaces in the UI. Warming the model once before the pool starts
would fix it. Not yet done.

Also worth watching: `agent_rule` is appended to the persona text OASIS injects, which
means the instruction text itself sits in the agent's context. No leak into post content
has been observed, but it is the kind of thing that shows up eventually.

## 16. `agents_per_hour_max` is the lever that controls memory

§10 blamed memory growth on rounds × agents. That is right, but the variable worth
tuning is neither: it is `agents_per_hour_max` in `time_config`, which caps how many
agents wake per round (`run_parallel_simulation.py:1063`).

Same 35-entity graph, same personas, same model, 24 rounds:

| `agents_per_hour_max` | rounds finished | peak OASIS RSS | actions |
|---|---|---|---|
| 25 (LLM's own value) | **died at 9/24** (OOM) | ~15 GB | 37 |
| **12** | **24/24 completed** | **~2.4 GB** | **994** |

Roughly **6× less memory and 27× more output**, on a host where the larger value could
not finish at all.

The reason is that memory tracks *posts accumulated × agents that must read them*, so
halving the agents awake per round cuts the product quadratically. Crucially, all 35
agents remain in the simulation — they simply take turns, so no perspective is lost.
That makes this a better lever than filtering `entity_types`, which removes voices
permanently and cannot be targeted (there is no centrality or importance ranking in
`entity_reader.py`, only type matching).

Working configuration for a 16 GB host with 35 agents:

    "agents_per_hour_max": 12,
    "minutes_per_round": 60,
    max_rounds: 24            # ~100 min, 994 actions, 2.4 GB

Two caveats. `simulation_config.json` is regenerated by the LLM on every `/prepare`,
so this edit is overwritten each time — it has to be reapplied after preparing, or
made overridable from `.env`. And a simulation killed by the OOM killer lands in status
`paused`, which `/start` refuses even with `force: true`; calling `/prepare` to clear it
regenerates all 35 personas (~14 minutes and the LLM cost) even though the existing
files are intact.

## 17. Thai output on the full run

The completed 24-round run (`deepseek-v4-flash-0731`, 994 actions) gives a much larger
language sample than the earlier partial runs:

| | agent-written posts | Thai | CJK fragments |
|---|---|---|---|
| Twitter | 63 | 100% | 6% |
| Reddit | 146 | 100% | 1% |
| **Total** | **209** | **100%** | **2%** |

Against `mimo-v2.5`'s 22%, that is an order of magnitude cleaner. Note most actions are
not posts at all — of 1,142 logged actions, 495 are `LIKE_POST` and only 209 produce
text, which is why action counts overstate LLM cost.

## 18. NER gets its own model (`NER_MODEL_NAME`)

Entity extraction is structured output, not prose, and the two jobs turn out to want
different models. Measured on the same Thai text and the real `NERExtractor` prompt:

| | `deepseek-v4-flash-0731` | `openai/gpt-oss-20b` |
|---|---|---|
| NER success | 2 of 3 | **3 of 3** |
| NER latency | 71 s | **20–52 s** |
| entities / relations | 6 / 2 | **7–8 / 2–7** |
| reasoning emitted | 6,735 chars | **185–338 chars** |
| report generation | **works** (9,427 chars, 3 sections) | **fails** |
| cost per job | $0.18 | $0.10 |

`deepseek` reasons at length — roughly twenty times more than `gpt-oss-20b` — which is
what pushed NER past the completion budget and caused the failures in §15 and §17.
But that same depth is what makes it able to write the report: `gpt-oss-20b` planned
only **1** section where the prompt demands 2–5, then returned no content at all during
the ReACT step (`finish_reason=stop`, not `length` — it simply had nothing to say).

So `NER_MODEL_NAME` overrides the model for entity extraction only. Empty (the default)
falls back to `LLM_MODEL_NAME` and reproduces upstream behaviour.

    LLM_MODEL_NAME=deepseek/deepseek-v4-flash-0731   # personas, reports, agents
    NER_MODEL_NAME=openai/gpt-oss-20b                # entity extraction only

This also makes graph memory update practical: NER is its bottleneck, so a full
simulation drops from roughly 4 hours to around 50 minutes.

**Caveat worth knowing.** Models classify entity types differently — `gpt-oss-20b` typed
`moc.go.th` as `GovernmentAgency` where `deepseek` did not surface it at all. Feeding
one model's NER into a graph another model built can leave the same entity with
inconsistent types. If that shows up, rebuild the whole graph under one model rather
than patching individual nodes.

## 19. Graph memory update verified end to end

With NER working, the feature from §13 completes its full path for the first time:

    [add_text] NER done: 13 entities, 6 relations
    [add_text] Chunk done: episode=78d1c8a1-...
    Successfully batch sent 5 activities to graph 3faa72b6-...

Neo4j before and after a 4-round run: **218 → 221 nodes, 99 → 105 relationships,
88 → 89 episodes, 127 → 129 entities**. The 13 entities NER found became 2 new ones
because the rest already existed and were merged correctly.

What agents say during a simulation now flows back into the knowledge graph as entities
and relations, not just as an opaque episode.

## 20. `LLM_MAX_TOKENS` has an optimum, not a floor

§18 raised the budget to 16384 to stop NER failing. That fixed NER but broke report
generation, and the reason is that the two stages of ReportAgent want opposite things.

Measured on the same simulation (`sim_daa52d11b060`, 1,608 actions) with
`deepseek-v4-flash-0731`:

| `LLM_MAX_TOKENS` | tool_call / llm_response | report | length |
|---|---|---|---|
| 4096 | 7 / 12 | **failed** — no content, `finish_reason=length` | 0 |
| 16384 | 3 / 19 | completed but unusable | 1,355 |
| **8192** | **15 / 25** | **completed** | **11,925** |

Too low and the model runs out of budget writing the section after tools return
(63 facts made the prompt large). Too high and it reasons past the tool-calling format
and emits `<tool_call>{...}</tool_call>` as literal prose, which the assembler writes
straight into the report — four sections of raw tool calls and no content.

8192 is the working value and is now the default in `config.py`. It is not a floor to
raise when something fails: check which stage failed first.

Residue at 8192: six stray `tool_call` strings and three sections hitting the iteration
cap, neither of which spoils the output.

## 21. Report needs the simulation environment alive

    WARNING: Interview API call failed (environment not running?):
             Simulation environment not running or closed

ReportAgent interviews agents as one of its tools. That only works while the simulation
process is still in wait mode after finishing its rounds. Generate the report before
stopping the simulation, or that tool silently contributes nothing — the report still
completes, just without interview evidence.

## 22. Capping simulation cost from `.env`

`/prepare` regenerates `simulation_config.json` every run, so editing that file by hand
never survives — a trap hit repeatedly while testing. Two values in `time_config` drive
both cost and memory, and they can now be capped from `.env` instead:

    SIM_AGENTS_PER_HOUR_MAX=12   # cap what the LLM generated (0 = leave alone)
    SIM_MINUTES_PER_ROUND=180    # override minutes per round (0 = leave alone)

Applied in `_parse_time_config` after the LLM's value and its own validation, so the cap
is a ceiling rather than a replacement.

**Why these two.** `agents_per_hour_max` is the single biggest lever (§16): halving it
from 25 to 12 cut peak memory roughly sixfold and let a 24-round run finish where it
previously died at round 9 — without removing any agent, since all of them remain and
simply take turns. `minutes_per_round` matters because a run starting at midnight with
60-minute rounds spends its first eight rounds in hours where no agent is awake:
measured 0 posts across rounds 1–8. At 180 minutes those dead hours are skipped, so 12
rounds cover 36 simulated hours instead of wasting a third of them.

## 23. What a run actually costs

Measured against OpenRouter's credits endpoint rather than estimated from token counts.
Between 11 and 13 September, usage went from **$70.65 to $76.79 — $6.14**, covering one
user simulation (123 personas, 15 rounds, 1,608 actions), three report generations, and
a long tail of testing. **A full run of that size is roughly $2.5–3.5.**

An earlier estimate in this file of ~$0.18 per job was wrong by more than an order of
magnitude. Three things it missed:

- prompts grow every round, because each awake agent reads the accumulated backlog —
  the same O(n²) that drives memory (§10)
- that run had 123 personas, not the 35 used for the estimate
- reasoning tokens are billed as completion, and `deepseek-v4-flash-0731` emits roughly
  6,735 characters of reasoning per call against `gpt-oss-20b`'s 185–338 (§18)

With the caps above, a comparable run should land near **$1–1.5**.

## 24. A full run, end to end, with every setting in place

First run where the caps, the split NER model, the 8192 budget and a live simulation
environment were all in effect at once. Project of 151 agents, 12 rounds at 180
simulated minutes each.

| stage | cost | result |
|---|---|---|
| personas + config (151) | $0.249 | 0 rule-based fallbacks |
| simulation (12 rounds) | $0.079 | 123 posts, 52,784 characters, 46 agents spoke |
| report | $0.049 | 15,077 characters, 5 sections, 29 evidence quotes |
| **total** | **$0.377** | |

The previous comparable run cost **$2.5–3.5**, so this is seven to nine times cheaper
for output that is better, not worse: Thai at **100%** (123/123 posts, against 98%
before) and the longest report produced in any test.

Peak OASIS memory was **1.7 GB** against the 15 GB that used to trigger the OOM killer.
`SIM_AGENTS_PER_HOUR_MAX` did the work — the LLM generated **50** for this run, which
would have been roughly twice the setting that already exhausted 16 GB.

## 25. Generate the report before stopping the simulation

§21 noted that ReportAgent's interview tool needs the simulation process alive. This run
measured what that is worth:

| | environment stopped | **environment alive** |
|---|---|---|
| report length | 11,925 | **15,077** |
| evidence quotes | 12 | **29** |

Three interviews completed, returning 69 KB of agent answers through the IPC channel.
The tool had never once succeeded before, because every earlier test generated its
report after stopping the simulation.

The order that works: prepare → run → **generate report** → stop.

One caveat seen here: `/api/report/<id>` reported `status: planning` for some time after
the run had actually finished — `section_05.md` and `full_report.md` were already on
disk. Check the report directory rather than trusting the status field alone.

## 26. The same content=None bug, in a third place

`report_agent.py` also calls the OpenAI client directly, so it hit the failure fixed
elsewhere:

    WARNING: LLM agent selection failed, using default selection:
             Model returned no content (finish_reason=length)

The report still completed — selection fell back to agents 0–5 by index instead of
choosing by relevance, which quietly lowers interview quality. Not yet fixed.

Files that call the client directly and therefore bypass `llm_client`'s guard:
`simulation_config_generator.py` (fixed), `report_agent.py` (not fixed),
`oasis_profile_generator.py` (has its own retry loop).
