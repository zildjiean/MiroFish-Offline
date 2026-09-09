# Thai language support

This fork can generate personas, agent posts and reports in Thai instead of English.

## Enabling it

```bash
# .env
CONTENT_LANGUAGE=th     # 'en' (default) reproduces upstream behaviour
```

Restart the backend afterwards. No other change is needed — including for the OASIS
simulation subprocess, which inherits the setting.

## What changes, and what deliberately does not

`CONTENT_LANGUAGE` only affects **free text**:

| Affected | Where |
|---|---|
| Persona `bio`, `persona`, `profession`, `interested_topics` | `services/oasis_profile_generator.py` |
| Seed posts (`initial_posts`) and `narrative_direction` | `services/simulation_config_generator.py` |
| What agents post during a simulation | follows the persona text — see below |
| Report outline, body and quotes | `services/report_agent.py` |
| Answers when chatting with the report agent | `services/report_agent.py` |

These stay English at every setting, because they are structural rather than prose:

| Kept English | Why |
|---|---|
| Ontology entity / edge type names | they become Neo4j labels and relationship types; Cypher would break |
| `gender` (`male` / `female` / `other`) | OASIS requires these exact values, and normalises to them |
| `country` | kept as an English country code |
| `poster_type` on seed posts | must match one of the English entity type names |
| The frontend UI | still hardcoded English strings — see "Not covered" below |

## Seed posts matter as much as personas

A simulation opens with `initial_posts` from `simulation_config.json`, replayed on both
platforms before any agent acts. Those are written by `simulation_config_generator.py`,
not by the personas — so translating personas alone leaves the first rounds in English
while later agent-written posts are Thai. Both files need the language rule.

## Guarding against script mixing

Most models available through OpenCode Zen are Chinese-trained, and they code-switch
mid-sentence when asked to write Thai — an early run produced Chinese fragments in
**21 of 35** personas (`รายงาน威胁 brief`, `我们会ตรวจสอบ`).

Two defences, both needed:

1. Each Thai prompt rule opens with an explicit instruction never to emit Chinese,
   Japanese or Korean characters. This alone removed contamination from the seed posts.
2. `Language.has_forbidden_script()` checks generated personas against a CJK/kana/hangul
   pattern, and `_generate_profile_with_llm` regenerates rather than shipping a
   contaminated persona — reusing the retry loop that already existed for bad JSON.

English is unaffected: `forbidden_script` is unset for it, so the check is a no-op.

### Measured effect, and the residue we accept

On a 35-entity graph with `mimo-v2.5`:

| | before | after |
|---|---|---|
| personas in Thai | 34/34 | 35/35 |
| personas with Chinese fragments | **21/35 (60%)** | **6/35 (17%)** |
| regenerations triggered | — | 30 |

The retry only fires while `attempt < max_attempts - 1`, so on the last of three
attempts a still-contaminated persona ships rather than failing the run. That, plus
30 regenerations for 35 personas, shows how often this model code-switches.

**17% is accepted deliberately** — the residue is short fragments (`采取采取`,
`我们将`) that do not stop the Thai reading. Raising `max_attempts` would cut it
further at the cost of quota and wall-clock. The real fix is a model that writes Thai
cleanly, and every model on OpenCode Zen is Chinese-trained (Qwen, GLM, Kimi, MiniMax,
DeepSeek, MiMo), so it is a provider change rather than a prompt change.

## Provider comparison, measured

The CJK problem turned out to be model-specific rather than a property of Chinese-trained
models in general. Measured on identical Thai persona prompts:

| provider / model | CJK contamination | tool calling | JSON | tok/s |
|---|---|---|---|---|
| OpenCode Zen `mimo-v2.5` | **17%** personas, **22%** agent posts | works | valid | ~40 |
| OpenRouter `deepseek/deepseek-v4-flash-0731` | **0%** personas, **0%** agent posts | 6/6 | 12/12 valid | ~38 |

Measured on the same 35-entity graph. With `mimo-v2.5` the CJK guard fired **65 times**
across 35 personas and still let 6 through; with `deepseek-v4-flash-0731` it fired
**zero times**, and all 13 agent posts sampled from a live run were clean Thai.

`deepseek-v4-flash-0731` writes clean Thai, so the CJK guard never fires against it. The
guard stays in place as a safety net — it costs nothing when there is nothing to catch,
and it protects anyone who switches back to a model that code-switches.

One observed quirk: occasionally a persona comes back missing `gender` and `country`
(both `None`). Harmless — `_normalize_gender(None)` returns `"other"` and the Reddit JSON
writer defaults `country` to `"US"` — but it makes that agent blander than intended.

## Moving to another provider (e.g. OpenRouter)

Nothing here is OpenCode-specific. To switch:

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=<key>
LLM_MODEL_NAME=<model>
OPENAI_API_BASE_URL=https://openrouter.ai/api/v1   # CAMEL-AI / OASIS read these
OPENAI_API_KEY=<key>
```

Then remove `OPENCODE_SESSION_ID` from `.env`. The header injection in
`.venv/.../mirofish_llm_headers.py` is env-driven and becomes a no-op once that is
unset, so it can stay in place. `LLM_USER_AGENT` is harmless to keep.

Embeddings are unaffected — they run locally and never touch the provider.

Two things to check on this host: whether FortiGuard sinkholes the new domain
(`getent hosts openrouter.ai`), and whether the chosen model supports **tool calling**,
which ReportAgent and OASIS both require.

`openrouter.ai` is **not** sinkholed here, so unlike `opencode.ai` it needs no
`/etc/hosts` pin — which removes the recurring DNS failure mode described in §1.

The trap when switching: `OPENAI_API_KEY` and `OPENAI_API_BASE_URL` are read by
CAMEL-AI/OASIS, not by MiroFish's own code. Changing only the `LLM_*` variables sends
personas and reports to the new provider while every simulation agent keeps calling the
old one — two bills, and confusing to debug.

## Agent posts need their own directive

A Thai persona is **not** enough on its own. OASIS builds the agent system prompt in
`social_platform/config/user.py` and the whole scaffold is English:

    # OBJECTIVE
    You're a Twitter user, and I'll present you with some posts...
    # SELF-DESCRIPTION
    Your name is X.
    Your have profile: {user_profile}        <- only this part is ours
    # RESPONSE METHOD
    Please perform actions by tool calling.

With only `{user_profile}` in Thai, the surrounding English pulls the model back:
measured **53%** of agent posts in Thai.

The fix is `Language.agent_rule`, appended to the two fields OASIS injects verbatim —
`user_char` in the Twitter CSV and `persona` in the Reddit JSON. That puts the
instruction inside the agent's own system prompt without patching OASIS, a dependency
we do not own. Result: **100%** of agent posts in Thai (63/63).

`agent_rule` is `""` for English, so upstream behaviour is unchanged.

Chinese fragments still appear in roughly a fifth of agent posts. The persona-level
CJK guard cannot help there: those posts are generated inside the OASIS subprocess,
which owns its own retry loop.

## How it is wired

`app/utils/language.py` holds one `Language` object per supported code, each carrying
the prompt fragments for that language. `get_language()` reads `CONTENT_LANGUAGE` at
call time.

Two different mechanisms are used, because the two prompt files differ:

- `oasis_profile_generator.py` builds its prompts with **f-strings**, so
  `{get_language().persona_rule}` interpolates directly.
- `report_agent.py` uses module-level constants applied with **`.format()`**. An
  expression in braces would raise `KeyError` there, so the language text is passed as
  a named `language_rule` placeholder from each call site instead.

Adding a language means adding one `Language` entry to `LANGUAGES` — no prompt surgery.

## Not covered

The frontend has **no i18n framework**; roughly 229 English strings are hardcoded
across `frontend/src/**/*.vue`, concentrated in `Step2EnvSetup.vue` (78),
`Step3Simulation.vue` (29) and `Process.vue` (29). Translating the UI means adding
`vue-i18n` and extracting those strings first. That work is independent of this change.

## Trade-offs worth knowing

- **Token cost roughly doubles.** Thai tokenises far less efficiently than English, so
  the same simulation consumes noticeably more of the provider quota and runs slower.
- **Model quality in Thai varies.** Open-weight models are generally weaker in Thai
  than in English. Check that personas read naturally before trusting a long run.
- **JSON escaping is marginally riskier** with Thai inside string values; the existing
  retry and `_fix_truncated_json` paths in the persona generator still apply.
