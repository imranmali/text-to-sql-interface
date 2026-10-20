"""Screening of natural-language questions before they reach the SQL generator.

Regex is a first filter only: the AST-based SQL guardrail and the read-only
connection remain authoritative. Patterns therefore favour precision on common
analytics phrasing ("orders that were updated", "deleted orders") over catching
every conceivable attack.
"""

from __future__ import annotations

import re
import unicodedata

from text2sql.models import GuardrailResult, Violation

_FLAGS = re.IGNORECASE

# Words that introduce an object, used between a verb and its target noun.
_DETERMINERS = r"(?:all|every|each|the|this|that|these|those|any|some|my|our|your|their)"
# Up to two filler words that are not prepositions, so "drop in data volume" passes.
_FILLER = r"(?:(?!(?:in|of|on|by|for|per|at|from|to|with)\b)\w+\s+){0,2}?"

PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, _FLAGS)
    for p in (
        # "ignore all previous instructions", "disregard the above rules"
        r"\b(?:ignore|disregard|forget|override|bypass)\b(?:\s+\w+){0,4}?\s+"
        r"(?:instructions?|rules?|prompts?|guidelines?|directions?|directives?|"
        r"constraints?|guardrails?|restrictions?|programming)\b",
        r"\bforget\s+(?:everything|all|what)\b",
        r"\byou\s+are\s+(?:now|no\s+longer)\b",
        r"\bfrom\s+now\s+on,?\s+(?:you|act|respond|answer)\b",
        # Role change. Plain "act as" is legal analytics phrasing ("customers who
        # act as resellers"), so require a privileged or assistant-like role.
        r"\b(?:act|acting|behave|respond)\s+as\s+(?:if\s+you\b|(?:an?\s+|the\s+|my\s+)?"
        r"(?:\w+\s+){0,2}?(?:admin|administrator|dba|system|assistant|ai|chatbot|root|"
        r"superuser|developer|hacker|unrestricted|unfiltered|jailbroken|evil)\b)",
        r"\bpretend\s+(?:to\s+be|you|that\s+you)\b",
        r"\brole[\s-]?play\b",
        # Prompt exfiltration.
        r"\bsystem\s+(?:prompt|message)\b",
        r"\b(?:reveal|show|print|display|output|repeat|leak|dump|tell\s+me)\s+(?:me\s+)?"
        r"(?:your|the\s+(?:initial|original|hidden|secret))\s+(?:\w+\s+)?"
        r"(?:prompt|instructions|rules|guidelines)\b",
        r"\bjail\s?break",
        r"\bdeveloper\s+mode\b",
        r"\bdo\s+anything\s+now\b",
        # Chat-template role tags and fake headers.
        r"<\s*/?\s*(?:system|assistant|user|instructions?)\s*>",
        r"<\|im_(?:start|end)\|>",
        r"\[/?(?:system|inst)\]",
        r"(?:^|\s)#{2,}\s*(?:system|instructions?|assistant)\b",
        r"\bnew\s+(?:instructions|rules|task|system\s+prompt)\s*:",
        r"(?-i:\bDAN\b)",  # case-sensitive so the name "Dan" is fine
    )
)

# Base-form verbs that request a write when used imperatively.
_IMPERATIVE_VERBS = (
    r"(?:delete|drop|truncate|update|modify|insert|alter|grant|revoke|remove|wipe|"
    r"erase|destroy|purge|overwrite|edit|rename)"
)
# Optional politeness / framing before an imperative verb at the start of a clause.
_IMPERATIVE_LEAD = (
    r"^(?:(?:please|kindly|now|then|also|and|just|ok(?:ay)?|so),?\s+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:you\s+)?to\s+|"
    r"let'?s\s+|go\s+ahead\s+and\s+|help\s+me\s+|try\s+to\s+)?(?:please\s+)?"
)
_TARGETS = (
    r"(?:tables?|databases?|db|schemas?|rows?|records?|entries|data|columns?|"
    r"index(?:es)?|views?|triggers?)"
)
_NOT_POSSESSIVE = r"(?!['\u2019]s\b)"

IMPERATIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, _FLAGS)
    for p in (
        _IMPERATIVE_LEAD + _IMPERATIVE_VERBS + r"\b" + _NOT_POSSESSIVE,
        # "add" alone is ambiguous ("add up revenue"), so require a record-like object.
        _IMPERATIVE_LEAD + r"add\s+(?:a|an|new|another|some|\d+)\s+(?:\w+\s+){0,2}?"
        r"(?:rows?|records?|entry|entries|columns?|users?|customers?|orders?|products?)\b",
        _IMPERATIVE_LEAD + r"change\s+(?:the|all|every|each|his|her|their|its|my)\b",
        _IMPERATIVE_LEAD + r"set\s+(?:\w+\s+){1,6}?(?:to|=)",
    )
)

DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, _FLAGS)
    for p in (
        # A write verb acting on a storage object, anywhere in the question.
        r"\b(?:delete|drop|truncate|update|modify|alter|insert|remove|wipe|erase|destroy|"
        r"purge|overwrite|empty)\s+(?:" + _DETERMINERS + r"\s+)*" + _FILLER + _TARGETS + r"\b",
        r"\b(?:delete|drop|truncate|wipe|erase|destroy|purge)\s+(?:all|everything)\b",
        r"\bcreate\s+(?:(?:a|an|new|the|temp|temporary)\s+)*(?:\w+\s+)?"
        r"(?:table|database|index|view|trigger|user|schema)\b",
        r"\b(?:grant|revoke)\b.{0,60}\b(?:access|privileges?|permissions?|roles?)\b",
        r"\badd\s+(?:\w+\s+){0,4}?to\s+the\s+\w+\s+table\b",
    )
)

EMBEDDED_SQL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, _FLAGS)
    for p in (
        # Stacked statements.
        r";\s*(?:select|insert|update|delete|drop|alter|create|truncate|attach|detach|"
        r"pragma|replace|grant|revoke|with|vacuum|begin|commit|exec(?:ute)?)\b",
        # Comment sequences used to cut off the rest of a statement.
        r"['\";)]\s*--",
        r"--\s*$",
        r"/\*|\*/",
        # Raw write/DDL statements.
        r"\b(?:drop|delete|insert|update|alter|truncate|create|attach|detach|replace)\s+"
        r"(?:or\s+\w+\s+)?(?:table|from|into|database|index|view|trigger|schema)\b",
        r"\bupdate\s+\w+\s+set\b",
        r"\bunion\s+(?:all\s+)?select\b",
        r"\bpragma\s+[a-z_]+",
        r"\bload_extension\s*\(",
        # Classic tautologies: ' OR '1'='1, OR 1=1.
        r"\bor\s+'?(\d+)'?\s*=\s*'?\1\b",
    )
)

_CLAUSE_SPLIT = re.compile(r"[.;!?\n]+|,\s*(?:and\s+)?then\b|\band\s+then\b")


def normalize(text: str) -> str:
    """NFKC-normalise, drop invisible format characters and collapse whitespace.

    This defeats simple obfuscation such as zero-width spaces inside keywords
    or full-width letters.
    """
    text = unicodedata.normalize("NFKC", text)
    chars = []
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cf":  # zero-width spaces/joiners, BOM, bidi controls, soft hyphen
            continue
        chars.append(" " if category == "Cc" and ch != "\n" else ch)
    text = "".join(chars)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line).strip()


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def _snippet(fragment: str, limit: int = 60) -> str:
    fragment = " ".join(fragment.split())
    return fragment if len(fragment) <= limit else fragment[: limit - 3] + "..."


class InputGuardrail:
    """Rejects empty, oversized, injected, destructive or SQL-smuggling questions."""

    def __init__(self, max_length: int = 1000) -> None:
        self.max_length = max_length

    def check(self, question: str) -> GuardrailResult:
        text = normalize(question) if isinstance(question, str) else ""
        if not text:
            return GuardrailResult.blocked(
                [Violation(type="empty_input", message="The question is empty.")]
            )

        violations: list[Violation] = []
        # Measure the raw input so padding with invisible characters still counts.
        if len(question.strip()) > self.max_length:
            violations.append(
                Violation(
                    type="input_too_long",
                    message=(
                        f"The question is {len(question.strip())} characters long; "
                        f"the maximum is {self.max_length}."
                    ),
                )
            )

        if found := _first_match(PROMPT_INJECTION_PATTERNS, text):
            violations.append(
                Violation(
                    type="prompt_injection",
                    message=f"Possible prompt injection detected: '{_snippet(found)}'.",
                )
            )

        if found := self._destructive_fragment(text):
            violations.append(
                Violation(
                    type="destructive_intent",
                    message=(
                        f"Only read-only questions are allowed; '{_snippet(found)}' "
                        "asks to modify data or schema."
                    ),
                )
            )

        if found := _first_match(EMBEDDED_SQL_PATTERNS, text):
            violations.append(
                Violation(
                    type="embedded_sql",
                    message=f"The question contains raw SQL or SQL syntax: '{_snippet(found)}'.",
                )
            )

        return GuardrailResult.blocked(violations) if violations else GuardrailResult.ok()

    @staticmethod
    def _destructive_fragment(text: str) -> str | None:
        # Imperative verbs only count at the start of a clause, so read-only uses
        # like "orders that were updated" or "last update date" pass.
        for clause in _CLAUSE_SPLIT.split(text):
            clause = clause.strip(" ,:-\"'")
            if clause and (found := _first_match(IMPERATIVE_PATTERNS, clause)):
                return found
        return _first_match(DESTRUCTIVE_PATTERNS, text)
