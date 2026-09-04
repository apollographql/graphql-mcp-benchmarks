#!/usr/bin/env python3
"""Shared keyword grammar for the two discovery conditions' search tools.

Why this file exists. `M-R2` and `M-G1` are only a *protocol* comparison if
their discovery tools are ergonomically identical, and the first version tried to
guarantee that by writing the same grammar twice — `openapi_search` in Python and
`schema_search` by shelling out to `rover schema search` with
`query.split()` as argv. Both therefore required **every** whitespace-separated
term to match, and the agent asks in task language: `flight number departure
gate`, `advisory grounding`, `pilot captain first officer`. Nothing matches all
of those at once, so **45% of M-R2's searches and 55% of M-G1's returned zero
results** across the whole matrix (NOTES.md 73).

That alone would have been a symmetric handicap. What made it a measurement
error is that the two conditions *recover* from an empty result at very different
prices: `M-R2` guesses a path — REST paths are guessable — or describes one
operation for ~4,760 B, while `M-G1` cannot guess a query and falls back to
`schema_describe(Query)`, the whole root type, for 18,410 B. Same bug, ~4x the
cost on the GraphQL side, which understated `M-G1`.

Grammar, now defined once:

  - A comma separates alternatives, as before: `Aircraft, CrewMember`.
  - Within an alternative, terms are **OR'd** and results are ranked by how many
    terms they matched. A four-word phrase returns its best matches instead of
    nothing. This is the behaviour change; everything else here is detail.
  - A term matches if it is a substring of the haystack, or if its stem is, so
    `advisory` finds `advisories` and `ratings` finds `typeRatings`. Substring
    matching is what handles camelCase, and the stem is what handles English
    plurals; neither covers the other.
  - Terms under three characters and a small stop list are dropped, because OR
    over `the` matches every description in the catalog and buries the signal.

Ranking is deterministic — matched-term count, then the shorter haystack (more
specific), then the coordinate — because the runs are at temperature 0 and a
search that reorders between replicates would show up as agent variance.
"""

# Checked longest-first: `advisories` must reach `advisor` via `ies`, not via `s`.
_SUFFIXES = ("ies", "ied", "es", "s", "y")

# Words that appear in the agent's phrasing and in nearly every description.
# Deliberately short: this is a stop list, not a language model.
_STOP = frozenset("""
and are but for from has have its not the that this was were what when which
with all any each every one two some only its their there then than into out
over under about across per via
""".split())


def stem(term: str) -> str:
    """Strip one English plural/adjectival suffix, or return the term unchanged.

    `advisory` and `advisories` both reduce to `advisor`, which is what makes
    them find each other. The length guard keeps `day` from becoming `d` and
    `gate` is untouched because `e` alone is not a suffix here.
    """
    for suffix in _SUFFIXES:
        if len(term) > len(suffix) + 2 and term.endswith(suffix):
            return term[: -len(suffix)]
    return term


def parse_query(raw: str) -> list[list[str]]:
    """`'a b, c'` -> `[['a','b'], ['c']]`, lowercased, stop words dropped.

    A clause that loses every one of its terms to the filters is dropped rather
    than kept as a clause that matches everything.
    """
    clauses = []
    for clause in raw.split(","):
        terms = [t for t in clause.lower().split()
                 if len(t) >= 3 and t not in _STOP]
        if terms:
            clauses.append(terms)
    return clauses


def score(haystack: str, clauses: list[list[str]]) -> int:
    """How many terms of the best-matching clause appear in `haystack`.

    0 means no match. `haystack` must already be lowercased by the caller, which
    is the one thing this module trusts it to do — the callers build haystacks
    from several fields and lowercase once.
    """
    best = 0
    for terms in clauses:
        hits = sum(1 for t in terms if t in haystack or stem(t) in haystack)
        best = max(best, hits)
    return best


def rank(scored: list[tuple[int, int, str, dict]], limit: int) -> list[dict]:
    """Order `(score, priority, haystack, payload)` tuples; return the payloads.

    Best score first, then lower `priority`, then the shorter haystack, then the
    haystack itself for a total order — replicates run at temperature 0 and a
    search that reorders between them would show up as agent variance.

    `priority` exists because the two surfaces differ in what is callable, which
    is a real protocol difference rather than a UX one. Every REST endpoint a
    search returns can be requested directly, so `openapi_search` passes 0 for
    all of them. In GraphQL only the `Query` roots are entry points, and a leaf
    like `Flight.gate` is useful only once you know which root reaches it, so
    `schema_search` ranks roots above fields above types. Before this existed,
    a phrase search buried `Query.flightsByNumbers` under twenty leaf fields.
    """
    ordered = sorted(scored, key=lambda s: (-s[0], s[1], len(s[2]), s[2]))
    return [payload for _, _, _, payload in ordered[:limit]]
