"""Unicode rule keys and normalized search with offsets into the original text."""
import unicodedata


def text_key(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold()


def normalized_text(text):
    """Keep normalization units intact, including combining marks and Hangul.

    Expansions such as ß → ss and ﬃ → ffi map back to their entire source
    character. Composition such as u + diaeresis maps to both source characters.
    """
    chunks = []
    start = 0
    current = ''
    for index, char in enumerate(text):
        first = unicodedata.normalize('NFKD', char)[0]
        joins = current and unicodedata.category(first).startswith('M')
        if current and not joins:
            # Canonical composition across starters is possible for Hangul.
            last = unicodedata.normalize('NFKC', current)[-1]
            joins = unicodedata.normalize('NFKC', last + first) != (
                unicodedata.normalize('NFKC', last) + unicodedata.normalize('NFKC', first))
        if current and not joins:
            chunks.append((start, index, unicodedata.normalize('NFKC', current).casefold()))
            current, start = '', index
        current += char
    if current:
        chunks.append((start, len(text), unicodedata.normalize('NFKC', current).casefold()))
    result, offsets = [], []
    for start, end, value in chunks:
        for char in value:
            if char.isspace():
                if result and result[-1] != ' ':
                    result.append(' '); offsets.append((start, end))
                elif offsets and result[-1] == ' ':
                    offsets[-1] = (offsets[-1][0], end)
            else:
                result.append(char); offsets.append((start, end))
    if result and result[-1] == ' ':
        result.pop(); offsets.pop()
    return ''.join(result), offsets


def normalized_matcher():
    # Scoped to one page/job; no document text survives in a global cache.
    cache = {}

    def matches(pattern, original):
        if original not in cache:
            cache[original] = normalized_text(original)
        text, offsets = cache[original]
        for match in pattern.finditer(text):
            start, end = match.span()
            # Never select half of an expanded/composed source character.
            if start == end or (start and offsets[start] == offsets[start-1]):
                continue
            if end < len(offsets) and offsets[end-1] == offsets[end]:
                continue
            yield offsets[start][0], offsets[end-1][1]
    return matches
