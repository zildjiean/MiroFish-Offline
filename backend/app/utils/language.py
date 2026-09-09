"""
Output language for generated content.

Upstream hardcodes English in the persona and report prompts. This module moves that
choice into `CONTENT_LANGUAGE` (default "en", which reproduces upstream behaviour) so
a deployment can generate personas, agent posts and reports in another language.

Only *free text* follows this setting. These stay English no matter what, because
they are structural rather than prose:

- ontology entity/edge type names — they become Neo4j labels and relationship types
- `gender` — OASIS requires "male" / "female" / "other"
- `country` — kept as an English country code

Agent post language needs no separate setting: OASIS builds each agent's system prompt
from its persona text, so personas written in Thai produce agents that post in Thai.
"""

import os
from typing import Dict


class Language:
    """Prompt fragments for one output language."""

    def __init__(self, code: str, english_name: str, native_name: str,
                 persona_rule: str, report_rule: str, chat_rule: str,
                 outline_rule: str, quote_rule: str):
        self.code = code
        self.english_name = english_name
        self.native_name = native_name
        self.persona_rule = persona_rule
        self.report_rule = report_rule
        self.chat_rule = chat_rule
        self.outline_rule = outline_rule
        self.quote_rule = quote_rule


ENGLISH = Language(
    code="en",
    english_name="English",
    native_name="English",
    persona_rule="Use English.",
    report_rule=(
        "   - The entire report MUST be written in English, regardless of source material language\n"
        "   - Tool-returned content may contain Chinese, mixed Chinese-English, or other languages\n"
        "   - When quoting tool-returned non-English content, ALWAYS translate it to fluent English before writing to report\n"
        "   - Keep original meaning unchanged during translation, ensure natural expression\n"
        "   - This rule applies to both body text and quoted content (> format)\n"
        "   - NEVER switch to Chinese or any other language mid-report"
    ),
    chat_rule="- ALWAYS respond in English, regardless of the language used in source material or report content",
    outline_rule=(
        "IMPORTANT: The entire report outline (title, summary, section titles and descriptions) "
        "MUST be in English. Never use Chinese or other languages."
    ),
    quote_rule="[Language Consistency - ALWAYS Write in English]",
)

THAI = Language(
    code="th",
    english_name="Thai",
    native_name="ภาษาไทย",
    persona_rule=(
        "Write every free-text field (bio, persona, profession, interested_topics) in natural, "
        "fluent Thai (ภาษาไทย) as a real Thai social media user would write it — not translated English. "
        "Keep personal names, organisation names, domains, product names and established technical terms "
        "in their original form rather than transliterating them. "
        "The following fields are structural and MUST stay in English regardless: "
        "gender (\"male\" / \"female\" / \"other\") and country (English country code such as \"TH\")."
    ),
    report_rule=(
        "   - The entire report MUST be written in natural, fluent Thai (ภาษาไทย)\n"
        "   - Tool-returned content may be in Thai, English or mixed; when quoting it, KEEP the original\n"
        "     wording so the reader sees what the agents actually said — do not translate quotes\n"
        "   - Write your own analysis and narration in Thai even when the quoted evidence is English\n"
        "   - Keep organisation names, domains and technical terms in their original form\n"
        "   - This rule applies to both body text and quoted content (> format)\n"
        "   - NEVER switch to Chinese; write Thai prose throughout"
    ),
    chat_rule="- ALWAYS respond in natural Thai (ภาษาไทย), regardless of the language used in source material or report content",
    outline_rule=(
        "IMPORTANT: The entire report outline (title, summary, section titles and descriptions) "
        "MUST be in natural Thai (ภาษาไทย). Never use Chinese."
    ),
    quote_rule="[Language Consistency - ALWAYS Write in Thai]",
)


LANGUAGES: Dict[str, Language] = {
    ENGLISH.code: ENGLISH,
    THAI.code: THAI,
}

DEFAULT_LANGUAGE_CODE = "en"


def get_language() -> Language:
    """Return the configured output language, falling back to English."""
    code = os.environ.get("CONTENT_LANGUAGE", DEFAULT_LANGUAGE_CODE).strip().lower()
    return LANGUAGES.get(code, ENGLISH)
