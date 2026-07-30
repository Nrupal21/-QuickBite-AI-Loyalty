"""QuickBite — LLM input sanitisation.

Sanitises user input before passing to OpenAI/Gemini to prevent
prompt injection attacks. Strips markdown, HTML, and control characters.

The review composer feeds customer-chosen tags straight into a prompt, so tags
are untrusted input on a path that ends in a model's instruction context. Two
layers guard it, because neither is sufficient alone:

1. **Structural** (here): strip the characters and shapes an injection needs —
   control characters, HTML/markdown, code fences, role labels — and cap the
   length. Deterministic, and cannot be talked out of.
2. **Positional** (ai_engine): the system prompt declares that everything in
   the user turn is data, and tags travel as a delimited list rather than as
   prose spliced into the instructions.

Sanitising is never perfect — an LLM has no hard boundary between data and
instruction — so the real containment is architectural: this endpoint's output
is a *draft shown to the person who asked for it*, never an action taken on
anyone's behalf. Nothing downstream is authorised by the model's output.
"""

import re
import unicodedata

# Phrases whose only purpose inside a restaurant tag is to retarget the model.
# A legitimate tag ("great biryani", "slow service") never contains these, so
# redacting them outright costs nothing real.
_INJECTION_PATTERNS = (
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions?",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above|earlier)",
    r"forget\s+(?:everything|all|your\s+instructions?)",
    r"\b(?:system|assistant|user)\s*:",  # chat role labels
    r"</?\s*(?:system|assistant|user|im_start|im_end)\s*>",  # ChatML-style markers
    r"you\s+are\s+now\s+(?:a|an)\b",
    r"new\s+instructions?\s*:",
    r"(?:reveal|print|repeat|output|show)\s+(?:the\s+)?(?:system\s+)?prompt",
)

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# Markdown/HTML structure an attacker uses to open a new "block" in the prompt.
_MARKUP_RE = re.compile(r"<[^>]*>|```|~~~|\[[^\]]*\]\([^)]*\)|^\s*[#>*-]+\s*", re.MULTILINE)

_WHITESPACE_RE = re.compile(r"\s+")

REDACTION = "[removed]"
MAX_TAG_LENGTH = 60


def _strip_control_characters(value: str) -> str:
    """Neutralise Unicode control and format characters.

    The two categories get opposite treatment, and the difference is what makes
    the keyword patterns work:

    - **Cf** (zero-width, bidi overrides) is *deleted*. Its attack is splitting
      a keyword invisibly — `sys<ZWSP>tem:` — so deleting rejoins the word and
      the pattern below matches it.
    - **Cc** (C0/C1 controls, including newline and tab) becomes a *space*.
      Deleting it would weld neighbouring words together: `nice\\n\\nSYSTEM:`
      would collapse to `niceSYSTEM:`, and the `\\b` anchor on the role-label
      pattern would no longer match — the injection would survive precisely
      because we scrubbed it.
    """
    out = []
    for ch in value:
        category = unicodedata.category(ch)
        if category == "Cf":
            continue
        out.append(" " if category == "Cc" else ch)
    return "".join(out)


def sanitise_tag(tag: str) -> str:
    """Return a tag that is safe to interpolate into a prompt.

    Order matters: normalise first so lookalike Unicode cannot hide a keyword
    from the pattern match; strip markup before matching so `i<b>gnore` does
    not slip through; truncate last so the cap applies to the final text rather
    than to markup that is about to disappear.
    """
    normalised = unicodedata.normalize("NFKC", tag)
    cleaned = _strip_control_characters(normalised)
    cleaned = _MARKUP_RE.sub(" ", cleaned)
    cleaned = _INJECTION_RE.sub(REDACTION, cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:MAX_TAG_LENGTH]


def sanitise_tags(tags: list[str]) -> list[str]:
    """Sanitise every tag, dropping any that sanitises down to nothing."""
    cleaned = [sanitise_tag(tag) for tag in tags]
    return [tag for tag in cleaned if tag and tag != REDACTION]


def contains_injection_attempt(tag: str) -> bool:
    """Whether a tag looked like an injection attempt — for logging and SEC-11.

    Kept separate from sanitising so a probe can be counted without blocking
    the request: a customer who types something odd still gets their review
    draft, and the security team still sees the signal.
    """
    normalised = unicodedata.normalize("NFKC", tag)
    cleaned = _MARKUP_RE.sub(" ", _strip_control_characters(normalised))
    return bool(_INJECTION_RE.search(cleaned))
