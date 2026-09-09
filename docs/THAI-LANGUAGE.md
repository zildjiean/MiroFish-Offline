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

## Why agent posts need no separate setting

OASIS builds each agent's system prompt from its persona text
(`social_agent/agent.py` → `user_info.to_system_message()`), and ships **no language
directive of its own** — verified by grepping the installed package. So a persona
written in Thai yields an agent that posts in Thai. Language flows from persona to
posts automatically.

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
