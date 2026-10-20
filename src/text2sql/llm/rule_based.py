"""A deterministic, offline SQL generator driven by keywords and the schema.

It needs no network access or API key and always produces the same SQL for
the same question, which makes it the default provider for demos, tests and
CI. It answers a deliberately narrow set of question shapes:

* counts - "how many", "count", "number of" (optionally grouped)
* aggregates - average / total (sum) / maximum / minimum of a numeric column
* top-N - "top 5 ...", "first 3 ...", "5 most expensive ..."
* listing - "list", "show", "which", "what are" ...
* grouping - "per X", "by X", "for each X", "each X"
* filters - "in the X category", "<column> is X", a known sample value
  ("delivered", "Germany"), a capitalised place after "from"/"in" for
  tables with location columns, and a year ("in 2024") on a date column

Everything is derived from the :class:`DatabaseSchema` passed in; only a few
generic table synonyms are hard-coded. Value filters can only recognise
values that appear in the schema's ``sample_values`` (a few per column) or
that are introduced by an explicit pattern such as "in the Books category".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from text2sql.models import SQLGeneration
from text2sql.schema import Column, DatabaseSchema, Table

UNANSWERABLE = "Could not map the question to any table in the schema."

#: Extra nouns that refer to a table, applied only if the table exists.
TABLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "customers": ("client", "clients", "user", "users", "buyer", "buyers"),
    "products": ("goods",),
    "orders": ("purchase", "purchases"),
    "order_items": ("line item", "line items"),
}

#: Columns that hold places, used for "customers from <Place>".
LOCATION_COLUMNS = ("city", "country", "state", "region", "province", "location", "town")

#: Column names too generic to identify a table on their own.
GENERIC_COLUMNS = frozenset({"id", "name"})

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twenty": 20,
}
_NUM = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"

STRUCTURE_WORDS = frozenset(
    {"by", "per", "in", "of", "from", "and", "or", "with", "for", "each", "every", "where"}
)

VALUE_STOPWORDS = frozenset(
    {"the", "a", "an", "each", "every", "all", "any", "same", "this", "that", "which", "what"}
)

SQL_KEYWORDS = frozenset(
    {
        "select",
        "from",
        "where",
        "group",
        "order",
        "by",
        "limit",
        "table",
        "index",
        "join",
        "on",
        "as",
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
        "case",
        "when",
        "values",
        "default",
        "check",
        "references",
    }
)

_INTENT_PATTERNS: dict[str, str] = {
    "count": r"\b(how many|count|number of)\b",
    "avg": r"\b(average|avg|mean)\b",
    "max": r"\b(maximum|max|highest|largest|biggest|most expensive|priciest)\b",
    "min": r"\b(minimum|min|lowest|smallest|cheapest|least expensive)\b",
    "sum": r"\b(total|sum)\b",
}
_TOP_N_PATTERNS = (
    rf"\b(?:top|first)\s+{_NUM}\b",
    rf"\b{_NUM}\s+(?:most|least|cheapest|priciest|highest|lowest|largest|biggest|smallest)\b",
)
_ASCENDING_WORDS = r"\b(least|cheapest|lowest|smallest|bottom|ascending|asc)\b"
_ROW_WORDS = r"\b(which|list|show|display|find|give|get)\b"
_GROUP_BY = r"\b(?:per|by|for each|for every|each|every)\s+(?:the\s+)?([a-z_]+(?:\s+[a-z_]+)?)"
_SORT_BY = r"\b(?:sorted|ordered|sort|order|rank|ranked)\s+by\s+(?:the\s+)?([a-z_]+(?:\s+[a-z_]+)?)"
_YEAR = r"\b((?:19|20)\d{2})\b"
_VALUE = r"""['"]?([A-Za-z0-9][\w&\-']*(?:\s+[A-Za-z0-9][\w&\-']*){0,3}?)['"]?"""
_PLACE = (
    r"\b(?:from|in|based in|located in|living in|live in|lives in)\s+"
    r"([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*)*)"
)


# --------------------------------------------------------------------------- helpers


def _inflections(word: str) -> set[str]:
    forms = {word}
    if word.endswith("ies"):
        forms.add(word[:-3] + "y")
    elif word.endswith("s"):
        forms.update({word[:-1], word + "es"})
        if word.endswith(("ses", "xes", "ches", "shes")):
            forms.add(word[:-2])
    elif word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        forms.add(word[:-1] + "ies")
    elif word.endswith(("x", "ch", "sh")):
        forms.add(word + "es")
    else:
        forms.add(word + "s")
    return {f for f in forms if f}


def _phrase_variants(name: str) -> set[str]:
    """Spellings of an identifier as it may appear in a question."""
    words = [w for w in re.split(r"[_\s]+", name.lower()) if w]
    if not words:
        return set()
    variants: set[str] = set()
    for last in _inflections(words[-1]):
        for joiner in (" ", "_"):
            variants.add(joiner.join([*words[:-1], last]))
    return variants


def _spans(phrase: str, text: str) -> list[tuple[int, int]]:
    pattern = r"(?<![\w])" + re.escape(phrase) + r"(?![\w])"
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _non_overlapping(matches: list[tuple[int, int, object]]) -> list[tuple[int, int, object]]:
    """Keep the longest matches first, dropping any that overlap a kept one."""
    kept: list[tuple[int, int, object]] = []
    for start, end, item in sorted(matches, key=lambda m: (m[0] - m[1], m[0])):
        if all(end <= s or start >= e for s, e, _ in kept):
            kept.append((start, end, item))
    return sorted(kept, key=lambda m: m[0])


def _quote_ident(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name.lower() not in SQL_KEYWORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_numeric(col: Column) -> bool:
    t = col.type.upper()
    return any(k in t for k in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC"))


def _is_text(col: Column) -> bool:
    t = col.type.upper()
    return not t or any(k in t for k in ("CHAR", "TEXT", "CLOB"))


def _is_key(col: Column) -> bool:
    return col.primary_key or col.name.lower() == "id" or col.name.lower().endswith("_id")


def _is_date(col: Column) -> bool:
    name, t = col.name.lower(), col.type.upper()
    return (
        "date" in name or name.endswith(("_at", "_time")) or t in ("DATE", "DATETIME", "TIMESTAMP")
    )


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


# --------------------------------------------------------------------------- analysis


@dataclass
class _Analysis:
    table: Table
    text: str  # lower-cased question
    original: str  # question with original casing
    table_spans: list[tuple[int, int]]
    mentions: list[Column] = field(default_factory=list)
    mention_spans: list[tuple[int, int]] = field(default_factory=list)


def _table_aliases(schema: DatabaseSchema) -> dict[str, set[str]]:
    last_words: dict[str, int] = {}
    for table in schema.tables.values():
        words = re.split(r"[_\s]+", table.name.lower())
        if len(words) > 1:
            last_words[words[-1]] = last_words.get(words[-1], 0) + 1
    single_names = {t.name.lower() for t in schema.tables.values()}

    aliases: dict[str, set[str]] = {}
    for key, table in schema.tables.items():
        names = _phrase_variants(table.name)
        words = re.split(r"[_\s]+", table.name.lower())
        if len(words) > 1 and last_words.get(words[-1]) == 1 and words[-1] not in single_names:
            names |= _phrase_variants(words[-1])
        for synonym in TABLE_SYNONYMS.get(key, ()):
            names |= _phrase_variants(synonym)
        aliases[key] = names
    return aliases


def _pick_table(schema: DatabaseSchema, text: str) -> tuple[Table, list[tuple[int, int]]] | None:
    matches: list[tuple[int, int, object]] = []
    for key, names in _table_aliases(schema).items():
        for name in names:
            matches.extend((s, e, key) for s, e in _spans(name, text))
    kept = _non_overlapping(matches)
    if kept:
        # A table named right after "per"/"by"/"each" is a grouping, not the subject.
        subject = [m for m in kept if not re.search(r"\b(per|by|each|every)\s+$", text[: m[0]])]
        _, _, key = (subject or kept)[0]
        table = schema.tables[str(key)]
        spans = [(s, e) for s, e, k in kept if k == key]
        return table, spans

    # No table named: fall back to columns that exist in exactly one table.
    owners: dict[str, list[str]] = {}
    for key, table in schema.tables.items():
        for col in table.columns:
            owners.setdefault(col.name.lower(), []).append(key)
    scores: dict[str, int] = {}
    for key, table in schema.tables.items():
        for col in table.columns:
            lname = col.name.lower()
            if _is_key(col) or lname in GENERIC_COLUMNS or len(owners[lname]) > 1:
                continue
            if any(_spans(v, text) for v in _phrase_variants(col.name)):
                scores[key] = scores.get(key, 0) + 1
    if not scores:
        return None
    best = max(scores.values())
    key = next(k for k in schema.tables if scores.get(k) == best)
    return schema.tables[key], []


def _column_variants(col: Column) -> set[str]:
    variants = _phrase_variants(col.name)
    lname = col.name.lower()
    if lname.endswith("_id") and len(lname) > 3:
        variants |= _phrase_variants(lname[:-3])
    return variants


def _resolve_column(table: Table, phrase: str) -> Column | None:
    phrase = phrase.strip().lower()
    candidates = [phrase]
    if " " in phrase:
        candidates.append(phrase.split()[0])
    for candidate in candidates:
        for col in table.columns:
            if candidate in _column_variants(col):
                return col
    return None


def _find_mentions(analysis: _Analysis) -> None:
    text = _blank(analysis.text, analysis.table_spans)
    matches: list[tuple[int, int, object]] = []
    for col in analysis.table.columns:
        if col.name.lower() == "id":
            continue
        for variant in _column_variants(col):
            matches.extend((s, e, col) for s, e in _spans(variant, text))
    kept = _non_overlapping(matches)
    seen: list[Column] = []
    for _, _, col in kept:
        if col not in seen:
            seen.append(col)  # type: ignore[arg-type]
    analysis.mentions = seen
    analysis.mention_spans = [(s, e) for s, e, _ in kept]


# --------------------------------------------------------------------------- filters


def _sample_casing(table: Table, col: Column, value: str) -> str:
    for sample in table.sample_values.get(col.name, []):
        if isinstance(sample, str) and sample.lower() == value.lower():
            return sample
    return value


def _filters(analysis: _Analysis, date_col: Column | None) -> list[str]:
    table, text, original = analysis.table, analysis.text, analysis.original
    clauses: list[str] = []
    filtered: set[str] = set()
    used_values: set[str] = set()
    # Words that cannot appear in an unquoted value: structure words plus the
    # table's own name and column names (e.g. "of orders in 2025 by status").
    blocked = set(STRUCTURE_WORDS)
    for variant in _phrase_variants(table.name).union(*map(_column_variants, table.columns)):
        blocked.update(variant.replace("_", " ").split())

    def add(col: Column, value: str, quoted: bool = False) -> bool:
        value = _sample_casing(table, col, value.strip().strip("'\"").strip())
        if not value or value.lower() in VALUE_STOPWORDS or col.name in filtered:
            return False
        if not quoted and any(w in blocked for w in value.lower().split()):
            return False
        clauses.append(f"{_quote_ident(col.name)} = {_literal(value)}")
        filtered.add(col.name)
        used_values.add(value.lower())
        return True

    text_cols = [c for c in table.columns if _is_text(c) and not _is_key(c)]

    # 1. Explicit patterns: "in the Books category", "status is shipped", ...
    for col in text_cols:
        for p in sorted(_column_variants(col)):
            p_re = re.escape(p)
            patterns = (
                rf"\b(?:in|from|of)\s+(?:the\s+)?{_VALUE}\s+{p_re}\b",
                rf"\b{p_re}\s+(?:(?:is|=|equals|equal to|named|called|of)\s+)?['\"]([^'\"]+)['\"]",
                rf"\b{p_re}\s+(?:is|=|equals|equal to|named|called|of)\s+{_VALUE}(?=[\s?.!,]|$)",
                rf"\bwith\s+(?:a\s+|the\s+)?{p_re}\s+(?:of\s+)?{_VALUE}(?=[\s?.!,]|$)",
            )
            for pattern in patterns:
                for m in re.finditer(pattern, original, flags=re.IGNORECASE):
                    quoted = m.start(1) > 0 and original[m.start(1) - 1] in "'\""
                    if add(col, m.group(1), quoted=quoted):
                        break
                if col.name in filtered:
                    break
            if col.name in filtered:
                break

    # 2. A known sample value mentioned anywhere ("delivered orders", "Germany").
    reserved = {v for spans_col in table.columns for v in _column_variants(spans_col)}
    for col in text_cols:
        if col.name in filtered:
            continue
        for sample in table.sample_values.get(col.name, []):
            if not isinstance(sample, str) or len(sample) < 2 or sample.isdigit():
                continue
            if sample.lower() in reserved or sample.lower() in used_values:
                continue
            if _spans(sample.lower(), text):
                add(col, sample)
                break

    # 3. "from <Place>" / "in <Place>" for tables with location columns.
    location = [c for c in text_cols if c.name.lower() in LOCATION_COLUMNS]
    if location and not any(c.name in filtered for c in location):
        for m in re.finditer(_PLACE, original):
            place = m.group(1).strip()
            if place.lower() in used_values or place.lower() in VALUE_STOPWORDS:
                continue
            parts = [f"{_quote_ident(c.name)} = {_literal(place)}" for c in location]
            clauses.append(parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")")
            used_values.add(place.lower())
            break

    # 4. A year on a date column.
    year = re.search(_YEAR, text)
    if year and date_col is not None:
        clauses.append(f"strftime('%Y', {_quote_ident(date_col.name)}) = {_literal(year.group(1))}")
    return clauses


# --------------------------------------------------------------------------- provider


class RuleBasedProvider:
    """Keyword- and schema-driven SQL generation. Deterministic and offline."""

    name = "rule_based"

    def generate(
        self,
        question: str,
        schema: DatabaseSchema,
        feedback: list[str] | None = None,
    ) -> SQLGeneration:
        sql, explanation = self._build(question or "", schema)
        if feedback and sql is not None:
            explanation = (
                explanation.rstrip(".")
                + f"; {len(feedback)} reported problem(s) from the previous attempt were"
                " considered, but this provider is deterministic."
            )
        return SQLGeneration(sql=sql, explanation=explanation, provider=self.name)

    # The heavy lifting is split out so ``generate`` stays readable.
    def _build(self, question: str, schema: DatabaseSchema) -> tuple[str | None, str]:
        original = " ".join(question.replace(chr(0x2019), "'").split())
        text = original.lower()
        picked = _pick_table(schema, text)
        if picked is None:
            return None, UNANSWERABLE
        table, table_spans = picked
        analysis = _Analysis(table=table, text=text, original=original, table_spans=table_spans)
        _find_mentions(analysis)

        intent_text = _blank(_blank(text, table_spans), analysis.mention_spans)
        group_col = self._group_column(analysis)
        numeric_mentions = [
            c for c in analysis.mentions if _is_numeric(c) and not _is_key(c) and c != group_col
        ]
        date_mentions = [c for c in analysis.mentions if _is_date(c)]
        date_col = (
            date_mentions[0]
            if date_mentions
            else next((c for c in table.columns if _is_date(c)), None)
        )
        where = _filters(analysis, date_col)
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        where_text = " where " + " and ".join(where) if where else ""
        t = _quote_ident(table.name)

        # Top-N ---------------------------------------------------------------
        top_n = self._top_n(text)
        if top_n is not None:
            col = self._numeric_column(table, numeric_mentions, text)
            if col is not None:
                direction = "ASC" if re.search(_ASCENDING_WORDS, text) else "DESC"
                sql = (
                    f"SELECT * FROM {t}{where_sql} "
                    f"ORDER BY {_quote_ident(col.name)} {direction} LIMIT {top_n}"
                )
                order = "lowest" if direction == "ASC" else "highest"
                return sql, (
                    f"Lists the {top_n} {table.name} rows with the {order} {col.name}{where_text}."
                )

        intent = next(
            (name for name, pat in _INTENT_PATTERNS.items() if re.search(pat, intent_text)), None
        )
        group_text = f", grouped by {group_col.name}" if group_col is not None else ""

        # Count ---------------------------------------------------------------
        if intent == "count":
            if group_col is not None:
                g = _quote_ident(group_col.name)
                sql = (
                    f"SELECT {g}, COUNT(*) AS count FROM {t}{where_sql} "
                    f"GROUP BY {g} ORDER BY count DESC"
                )
            else:
                sql = f"SELECT COUNT(*) AS count FROM {t}{where_sql}"
            return sql, f"Counts the rows in the {table.name} table{where_text}{group_text}."

        # Aggregates ----------------------------------------------------------
        if intent in ("avg", "sum", "max", "min"):
            col = self._numeric_column(table, numeric_mentions, text)
            if col is not None:
                wants_row = (
                    intent in ("max", "min")
                    and group_col is None
                    and (not numeric_mentions or re.search(_ROW_WORDS, text))
                )
                c = _quote_ident(col.name)
                if wants_row:
                    direction = "DESC" if intent == "max" else "ASC"
                    sql = f"SELECT * FROM {t}{where_sql} ORDER BY {c} {direction} LIMIT 1"
                    word = "highest" if intent == "max" else "lowest"
                    return sql, (
                        f"Returns the {table.name} row with the {word} {col.name}{where_text}."
                    )
                func, prefix, word = {
                    "avg": ("AVG", "avg", "average"),
                    "sum": ("SUM", "total", "total"),
                    "max": ("MAX", "max", "maximum"),
                    "min": ("MIN", "min", "minimum"),
                }[intent]
                expr = f"ROUND(AVG({c}), 2)" if func == "AVG" else f"{func}({c})"
                alias = f"{prefix}_{col.name.lower()}"
                if not re.fullmatch(r"[a-z_][a-z0-9_]*", alias):
                    alias = prefix
                if group_col is not None:
                    g = _quote_ident(group_col.name)
                    sql = (
                        f"SELECT {g}, {expr} AS {alias} FROM {t}{where_sql} "
                        f"GROUP BY {g} ORDER BY {alias} DESC"
                    )
                else:
                    sql = f"SELECT {expr} AS {alias} FROM {t}{where_sql}"
                return sql, (
                    f"Computes the {word} {col.name} of {table.name}{where_text}{group_text}."
                )

        # Listing (default) ---------------------------------------------------
        sql = f"SELECT * FROM {t}{where_sql}"
        sort_col = self._sort_column(analysis)
        sort_text = ""
        if sort_col is not None:
            direction = (
                " DESC" if re.search(r"\b(desc|descending|highest|most|largest)\b", text) else ""
            )
            sql += f" ORDER BY {_quote_ident(sort_col.name)}{direction}"
            sort_text = f", ordered by {sort_col.name}{' descending' if direction else ''}"
        return sql, f"Lists rows from the {table.name} table{where_text}{sort_text}."

    @staticmethod
    def _top_n(text: str) -> int | None:
        for pattern in _TOP_N_PATTERNS:
            m = re.search(pattern, text)
            if m:
                return max(1, _to_int(m.group(1)))
        if re.search(r"\btop\b", text):
            return 10
        return None

    @staticmethod
    def _group_column(analysis: _Analysis) -> Column | None:
        for m in re.finditer(_GROUP_BY, analysis.text):
            if re.search(
                r"\b(sorted|ordered|sort|order|rank|ranked)\s+$", analysis.text[: m.start()]
            ):
                continue
            col = _resolve_column(analysis.table, m.group(1))
            if col is not None:
                return col
        return None

    @staticmethod
    def _sort_column(analysis: _Analysis) -> Column | None:
        m = re.search(_SORT_BY, analysis.text) or re.search(_GROUP_BY, analysis.text)
        return _resolve_column(analysis.table, m.group(1)) if m else None

    @staticmethod
    def _numeric_column(table: Table, mentioned: list[Column], text: str) -> Column | None:
        if mentioned:
            return mentioned[0]
        candidates = [c for c in table.columns if _is_numeric(c) and not _is_key(c)]
        if not candidates:
            return None
        hints = (
            (r"expensive|cheap|pric|cost", ("price", "cost")),
            (r"amount|revenue|sales|spen[dt]|value|total", ("amount", "total", "revenue")),
            (r"quantit|units|sold", ("quantity", "qty", "units")),
        )
        for pattern, names in hints:
            if re.search(pattern, text):
                for col in candidates:
                    if any(n in col.name.lower() for n in names):
                        return col
        real = [c for c in candidates if any(k in c.type.upper() for k in ("REAL", "FLOA", "DOUB"))]
        return (real or candidates)[0]
