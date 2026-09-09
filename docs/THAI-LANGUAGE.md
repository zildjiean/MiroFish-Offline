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
| What agents post during a simulation | follows the persona text — see below |
| Report outline, body and quotes | `services/report_agent.py` |
| Answers when chatting with the report agent | `services/report_agent.py` |

These stay English at every setting, because they are structural rather than prose:

| Kept English | Why |
|---|---|
| Ontology entity / edge type names | they become Neo4j labels and relationship types; Cypher would break |
| `gender` (`male` / `female` / `other`) | OASIS requires these exact values, and normalises to them |
| `country` | kept as an English country code |
| The frontend UI | still hardcoded English strings — see "Not covered" below |

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
