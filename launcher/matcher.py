"""
String Matcher - Scores how well a query matches a name

Two kinds of match are scored:

    Acronym      "vsc" matches the initials of visual_studio_config
    Subsequence  "vsc" matches v..s..c in order, scored by how tightly
                 packed and how early in the name those letters are

Not Levenshtein distance: a user typing "vsc" has not made three typos, they
have typed an abbreviation. Position and compactness matter more than edit count.
"""

from typing import List, Optional, Tuple


# CONSTANTS - Centralized configuration for easy maintenance

# Characters that separate words inside a filename or application name
WORD_SEPARATORS = " _-.()[]{}"

# Set above the highest score the bonuses can reach, so an exact match always
# wins without clamping partial scores (which would lose their ordering)
SCORE_EXACT = 200

# Base used by the positional formula. Bonuses are added on top of this.
SCORE_BASE = 100

# Bonus when the matched characters land close together in a short name.
# A query that is nearly the whole name is a better answer than the same query
# buried inside a much longer one.
LENGTH_BONUS_CLOSE = 20    # Name is within 5 characters of the query
LENGTH_BONUS_NEAR = 10     # Name is within 10 characters of the query

# Bonus per query character when every character matched. Long queries are
# capped so that typing more does not automatically beat a better match.
CONTIGUOUS_BONUS_PER_CHAR = 10
CONTIGUOUS_BONUS_THRESHOLD = 4     # After this many characters the bonus halves
CONTIGUOUS_BONUS_TAIL = 5

# Minimum score to count as a match at all. Below this the result is noise.
MIN_SCORE = 30

# How tightly packed a subsequence must be before it counts as a real match.
# 0.34 means the matched characters may span at most about three times the
# query length, so "msr" may match "my_status_report" but not three scattered
# letters inside "Administrative Tools".
MIN_DENSITY = 0.34


class MatchResult:
    """The outcome of matching a query against one name."""

    __slots__ = ("score", "indices")

    def __init__(self, score: int = 0, indices: Optional[List[int]] = None):
        self.score = score
        self.indices = indices or []      # Positions that matched, for highlighting

    def __bool__(self) -> bool:
        return self.score > 0

    def __repr__(self) -> str:
        return "MatchResult(score=%d, indices=%r)" % (self.score, self.indices)


NO_MATCH = MatchResult(0)


def match(query: str, text: str) -> MatchResult:
    """Score how well `query` matches `text`. Returns 0 when it does not.

    Both the acronym and subsequence strategies are tried and the better score
    wins, because the same query can be both. Typing "code" against
    "Visual Studio Code" is a word match, while "vsc" is an acronym.
    """
    if not query or not text:
        return NO_MATCH

    q = query.lower().strip()
    t = text.lower()

    if not q:
        return NO_MATCH

    # A whole-name match cannot be beaten, so return immediately
    if q == t:
        return MatchResult(SCORE_EXACT, list(range(len(t))))

    best = NO_MATCH

    acronym = _match_acronym(q, t)
    if acronym.score > best.score:
        best = acronym

    subsequence = _match_subsequence(q, t)
    if subsequence.score > best.score:
        best = subsequence

    return best if best.score >= MIN_SCORE else NO_MATCH


# ACRONYM MATCHING


def _word_starts(text: str) -> List[int]:
    """First character index of each word. Splits on separators, not just
    spaces, because filenames rarely contain spaces."""
    starts = []
    previous_was_separator = True

    for i, char in enumerate(text):
        if char in WORD_SEPARATORS:
            previous_was_separator = True
            continue
        if previous_was_separator:
            starts.append(i)
        previous_was_separator = False

    return starts


def _match_acronym(q: str, t: str) -> MatchResult:
    """Match the query against the initials of each word in the name.

    Scored by the proportion of words consumed, so "vsc" beats "vs".
    """
    starts = _word_starts(t)
    if len(starts) < 2:
        # A single word has no meaningful acronym; "n" should not match "notepad"
        return NO_MATCH

    initials = "".join(t[i] for i in starts)
    if not initials.startswith(q):
        return NO_MATCH

    matched_words = len(q)
    total_words = len(initials)
    score = matched_words * SCORE_BASE // total_words

    # Using every word is a complete answer, so lift it above partial acronyms
    if matched_words == total_words:
        score += LENGTH_BONUS_CLOSE

    return MatchResult(score, starts[:matched_words])


# SUBSEQUENCE MATCHING


def _match_subsequence(q: str, t: str) -> MatchResult:
    """Find the query characters inside the name, in order but not adjacent.

    Two passes, best wins: word-start-preferring (so "vsc" hits the initials),
    and plain leftmost (the safety net, since preferring word starts can jump
    past a letter the query still needs).
    """
    # A contiguous match is always legitimate ("port" finding "export.txt")
    contiguous_at = t.find(q.replace(" ", ""))

    candidates = []
    for prefer_word_starts in (True, False):
        indices = _find_indices(q, t, prefer_word_starts)
        if indices is None:
            continue
        # A gapped match must start on a word boundary, or it is noise
        gapped = indices[-1] - indices[0] + 1 > len(indices)
        if gapped and contiguous_at < 0 and indices[0] not in _word_start_set(t):
            continue
        candidates.append(indices)

    if not candidates:
        return NO_MATCH

    return max(
        (_score_indices(q, t, idx) for idx in candidates),
        key=lambda r: r.score,
    )


def _word_start_set(text: str) -> set:
    """Word start positions as a set, for membership tests."""
    return set(_word_starts(text))


def _score_indices(q: str, t: str, indices: List[int]) -> MatchResult:
    """Turn a set of matched positions into a score."""
    first_index = indices[0]
    span = indices[-1] - indices[0] + 1

    # Flow Launcher's formula. A match starting early scores higher, and a
    # tightly packed match scores higher than one spread across the name.
    score = SCORE_BASE * (len(q) + 1) // ((1 + first_index) + (span + 1))

    # Reward a query that is nearly the whole name
    slack = len(t) - len(q)
    if slack < 5:
        score += LENGTH_BONUS_CLOSE
    elif slack < 10:
        score += LENGTH_BONUS_NEAR

    # Reward matching more characters, with diminishing returns so a long query
    # cannot outscore a genuinely better match simply by being long.
    count = len(q.replace(" ", ""))
    if count <= CONTIGUOUS_BONUS_THRESHOLD:
        bonus = count * CONTIGUOUS_BONUS_PER_CHAR
    else:
        bonus = (
            CONTIGUOUS_BONUS_THRESHOLD * CONTIGUOUS_BONUS_PER_CHAR
            + (count - CONTIGUOUS_BONUS_THRESHOLD) * CONTIGUOUS_BONUS_TAIL
        )

    # Scale the bonus by how tightly packed the match is (1.0 = adjacent)
    density = count / span if span else 1.0
    score += int(bonus * density)

    # A loose match that also starts late in the name is almost always noise
    if density < MIN_DENSITY and first_index > 0:
        return NO_MATCH

    return MatchResult(score, indices)


def _find_indices(
    q: str, t: str, prefer_word_starts: bool
) -> Optional[List[int]]:
    """Locate each query character in order.

    Returns the matched positions, or None if the characters do not all appear
    in order. See `_match_subsequence` for why both modes are needed.
    """
    starts = set(_word_starts(t)) if prefer_word_starts else set()
    indices = []
    cursor = 0

    for char in q:
        if char == " ":
            continue

        found = _next_index(t, char, cursor, starts)
        if found is None:
            return None

        indices.append(found)
        cursor = found + 1

    return indices or None


def _next_index(t: str, char: str, cursor: int, starts: set) -> Optional[int]:
    """Find `char` at or after `cursor`, preferring a word start if asked."""
    fallback = None

    for i in range(cursor, len(t)):
        if t[i] != char:
            continue
        if i in starts:
            return i              # Word boundary wins when we are looking for one
        if fallback is None:
            fallback = i          # Otherwise remember the leftmost occurrence

    return fallback


# CONVENIENCE


def score(query: str, text: str) -> int:
    """Return just the numeric score, for callers that ignore highlighting."""
    return match(query, text).score


def best_of(query: str, texts: List[str]) -> Tuple[int, int]:
    """Score a query against several names, returning (best score, its index)."""
    best_score = 0
    best_index = -1
    for i, text in enumerate(texts):
        s = score(query, text)
        if s > best_score:
            best_score, best_index = s, i
    return best_score, best_index
