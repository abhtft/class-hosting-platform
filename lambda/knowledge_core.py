"""The class knowledge service engine: everything that decides the QUALITY of retrieval, as pure functions (standard library only, no AWS, no network).

  pages of text --clean_pages--> --chunk_pages--> passages (page, section, text)
  passages --tokenize--> build_bm25            (keyword search index)
  passages --embedding model--> pack_vectors   (vector search index, float16)
  question --hybrid_search--> the best passages, each with page, section and a `confident` flag

Why this file is separate: the API function, the ingestion worker and the tests all use it, and a change here (the chunk size, the tokenizer, the
fusion) changes answers, so it is covered by tests that check behaviour on a small study-book corpus. Sizes are counted in WORDS (a word is about 1.3
tokens in English); `estimate_tokens` is for cost estimates only.
"""
import math
import operator
import re
import struct
from array import array

# ------------------------------------------------------------------ words

STOPWORDS = frozenset("""a about above after again against all also am an and any are as at be because been before being below between both but by can could did do does doing
down during each few for from further had has have having he her here hers him his how i if in into is it its itself just me more most my no nor not of off on once only or other our
out over own same she should so some such than that the their theirs them then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours""".split())

WORD = re.compile(r"[a-z0-9]+")


def stem(word):
    """A light, consistent suffix stripper (the same function indexes passages and questions, so it only has to be consistent, not linguistic).
    Plural, "-ing", "-ed" and a final "e" go, so make, makes and making meet."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    else:
        for suffix in ("ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[:-len(suffix)]
                break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def tokenize(text, keep_stopwords=False):
    """Lowercase words and numbers, stopwords dropped, lightly stemmed. Numbers stay (years and quantities matter in study books)."""
    words = WORD.findall(str(text).lower())
    return [stem(w) for w in words if keep_stopwords or w not in STOPWORDS]


def estimate_tokens(text):
    """About four characters per token in English: good enough for cost estimates and limits, never used to cut text."""
    return math.ceil(len(text) / 4)


# ------------------------------------------------------------------ cleaning

_SPACES = re.compile(r"[ \t  -​ 　]+")
# "12", "Page 12", "12 of 40", "(iv)", "- 5 -", "[12]", "xiv": strict roman numerals only, so a heading such as "Civil" is never taken for one
_ROMAN = r"(?=[ivxlc])c{0,2}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_PAGE_NUMBER_LINE = re.compile(rf"^[(\[\-–—\s]*(?:page\s+)?(?:\d{{1,4}}|{_ROMAN})(?:\s+of\s+\d{{1,4}})?[)\]\-–—\s]*$", re.I)


def _normal_line(line):
    return re.sub(r"\d+", "#", _SPACES.sub(" ", line).strip().lower())


def _edges(lines):
    """Indexes of the first two and the last two non-empty lines of a page: the only places a running header, a footer or a page number lives."""
    filled = [i for i, line in enumerate(lines) if line]
    return set(filled[:2] + filled[-2:])


def clean_pages(pages):
    """pages: [(page_number, text)]. Returns [(page_number, text)] with joined hyphenated words, page-number lines and repeated running headers or
    footers removed. Only the first two and last two lines of a page are ever removed, and only when the same short line (ignoring digits) is at the
    edge of at least 30 percent of the pages and at least 3 pages: a label like "Solution:" in the middle of a page is real text and stays."""
    prepared = []
    for number, text in pages:
        lines = [_SPACES.sub(" ", line).strip() for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        prepared.append((number, lines, _edges(lines)))
    counts = {}
    for _, lines, edge in prepared:
        for key in {_normal_line(lines[i]) for i in edge if len(lines[i]) <= 80}:
            counts[key] = counts.get(key, 0) + 1
    repeated = {key for key, seen in counts.items() if seen >= 3 and seen >= 0.3 * len(prepared) and key}
    cleaned = []
    for number, lines, edge in prepared:
        kept = []
        for i, line in enumerate(lines):
            if i in edge and (_PAGE_NUMBER_LINE.match(line) or (len(line) <= 80 and _normal_line(line) in repeated)):
                continue
            kept.append(line)
        text = "\n".join(kept)
        text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)  # "photo-\nsynthesis" -> "photosynthesis"
        cleaned.append((number, text))
    return cleaned


# ------------------------------------------------------------------ chunking

_HEADING_WORDS = re.compile(r"^(chapter|unit|section|lesson|topic|part)\s+([0-9]+|[ivxlc]+)\b", re.I)
_HEADING_NUMBERED = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Za-z]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def is_heading(line):
    """A short line that names a section: "Chapter 3 ...", "3.1 Photosynthesis" (no final period), or a short all-capitals line."""
    line = line.strip()
    words = line.split()
    if not line or len(line) > 90 or len(words) > 12:
        return False
    if _HEADING_WORDS.match(line):
        return True
    if _HEADING_NUMBERED.match(line) and not line.endswith((".", ",", ";", ":")) and len(words) >= 2:
        return True
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and len(words) <= 10 and all(c.isupper() for c in letters) and not line.endswith((".", ","))


HEADING_MARK = "\u0001"  # a line that starts with this IS a heading (markdown and HTML know; a PDF has to be guessed at by is_heading)


def _blocks(pages):
    """[(page, text, is_heading)]: paragraphs (lines joined; a blank line or a heading ends one) in reading order, with the page each started on."""
    blocks = []
    for number, text in pages:
        buffer = []
        for raw in str(text).split("\n"):
            line = raw.strip()
            if line.startswith(HEADING_MARK):
                line = line[1:].strip()
                if buffer:
                    blocks.append((number, " ".join(buffer), False))
                    buffer = []
                if line:
                    blocks.append((number, line, True))
            elif not line:
                if buffer:
                    blocks.append((number, " ".join(buffer), False))
                    buffer = []
            elif is_heading(line):
                if buffer:
                    blocks.append((number, " ".join(buffer), False))
                    buffer = []
                blocks.append((number, line, True))
            else:
                buffer.append(line)
        if buffer:
            blocks.append((number, " ".join(buffer), False))
    return blocks


def _sentences(text, limit_words):
    """Sentences of a paragraph; a sentence longer than `limit_words` is cut at word boundaries so no unit is bigger than a chunk."""
    out = []
    for sentence in _SENTENCE_END.split(text.strip()):
        words = sentence.split()
        for start in range(0, len(words), limit_words):
            piece = " ".join(words[start:start + limit_words])
            if piece:
                out.append(piece)
    return out


def chunk_pages(pages, target_words=300, overlap_words=40, min_words=40, page_break_words=100):
    """pages: [(page_number, text)] (already cleaned). Returns passages [{"n", "page", "page_end", "section", "text", "words"}] in reading order.

    Rules: whole sentences only; about `target_words` per passage; the last sentences (up to `overlap_words`) of a passage start the next one inside the
    same section; a new section (a heading) always starts a new passage and is never left at the end of one; a too-short tail joins the passage before
    it when they share a section. The heading text is kept in `section` (and `embed_text` puts it in front of the passage for the embedding model).
    A page break also ends a passage when the page ended with a complete sentence and the passage already has `page_break_words` words: citations stay
    precise (one page, not two) and unrelated pages stop merging (found on a real textbook: an imprint page was joined to the foreword)."""
    chunks = []
    section = ""
    current = []  # [(sentence, page)]
    carried = 0   # how many leading sentences of `current` are overlap from the previous passage

    def words_of(items):
        return sum(len(s.split()) for s, _ in items)

    def emit(final=False):
        nonlocal current, carried
        fresh = current[carried:]
        if not fresh:
            current, carried = [], 0
            return
        text = " ".join(s for s, _ in current)
        piece = {"page": current[0][1], "page_end": current[-1][1], "section": section, "text": text, "words": len(text.split())}
        if chunks and chunks[-1]["section"] == section and words_of(fresh) < min_words and carried:
            previous = chunks[-1]  # a tail made only of a few new sentences: fold it into the previous passage
            previous["text"] = previous["text"] + " " + " ".join(s for s, _ in fresh)
            previous["page_end"] = fresh[-1][1]
            previous["words"] = len(previous["text"].split())
        else:
            chunks.append(piece)
        if final:
            current, carried = [], 0
            return
        tail, total = [], 0
        for item in reversed(current) if overlap_words > 0 else []:
            size = len(item[0].split())
            if total + size > overlap_words:  # a last sentence longer than the overlap carries nothing: an overlap is a hint, not a second copy
                break
            tail.insert(0, item)
            total += size
        current, carried = tail, len(tail)

    for page, text, heading in _blocks(pages):
        if heading:
            if words_of(current[carried:]) > 0:
                emit(final=True)
            else:
                current, carried = [], 0
            section = text
            continue
        for sentence in _sentences(text, target_words):
            size = len(sentence.split())
            fresh = current[carried:]
            if fresh and page != fresh[-1][1] and fresh[-1][0].rstrip().endswith((".", "!", "?", ":", "”", '"')) and words_of(fresh) >= page_break_words:
                emit(final=True)  # a new page after a finished sentence: start clean, with no overlap from the page before
            elif fresh and words_of(current) + size > target_words and words_of(fresh) >= min_words:
                emit()
            current.append((sentence, page))
    if words_of(current[carried:]) > 0:
        emit(final=True)
    for n, chunk in enumerate(chunks):
        chunk["n"] = n
    return chunks


def embed_text(chunk):
    """What the embedding model sees: the section title in front of the passage (it carries the topic a mid-chapter paragraph does not repeat)."""
    return f"{chunk['section']}\n{chunk['text']}" if chunk.get("section") else chunk["text"]


# ------------------------------------------------------------------ keyword search (BM25)

def build_bm25(token_lists):
    """token_lists: one list of terms per passage. Returns a JSON-friendly index."""
    postings, lengths = {}, []
    for number, terms in enumerate(token_lists):
        lengths.append(len(terms))
        counts = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        for term, tf in counts.items():
            postings.setdefault(term, []).append([number, tf])
    size = len(lengths)
    return {"n": size, "avgdl": (sum(lengths) / size) if size else 0.0, "dl": lengths, "postings": postings}


def bm25_scores(index, terms, k1=1.5, b=0.75):
    """{passage number: score} for the passages that contain at least one query term."""
    scores = {}
    size, avgdl = index["n"], index["avgdl"] or 1.0
    for term in set(terms):
        posting = index["postings"].get(term)
        if not posting:
            continue
        idf = math.log(1 + (size - len(posting) + 0.5) / (len(posting) + 0.5))
        for number, tf in posting:
            norm = tf + k1 * (1 - b + b * index["dl"][number] / avgdl)
            scores[number] = scores.get(number, 0.0) + idf * tf * (k1 + 1) / norm
    return scores


# ------------------------------------------------------------------ vectors (float16, normalised)

def normalize(vector):
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else [0.0] * len(vector)


def pack_vectors(rows):
    """Rows of equal length -> bytes (float16, little endian), normalised first so a dot product is the cosine."""
    if not rows:
        return b""
    dims = len(rows[0])
    out = []
    for row in rows:
        if len(row) != dims:
            raise ValueError("all vectors must have the same number of dimensions")
        out.append(struct.pack(f"<{dims}e", *normalize(row)))
    return b"".join(out)


def unpack_vectors(data, dims, compact=False):
    """Rows of floats from pack_vectors() bytes. compact=True keeps each row as a 4-byte-per-number array (a Python tuple of floats costs about
    32 bytes per number: 3000 passages of 768 numbers would be 75 MB instead of 9 MB); the search code accepts both."""
    row_bytes = 2 * dims
    if dims <= 0 or len(data) % row_bytes:
        raise ValueError("the vector file does not match the dimension")
    rows = (struct.unpack_from(f"<{dims}e", data, offset) for offset in range(0, len(data), row_bytes))
    return [array("f", row) for row in rows] if compact else list(rows)


def vector_scores(query, rows):
    """Cosine similarity of a query vector with every row (rows are normalised when packed; the query is normalised here)."""
    q = normalize(query)
    return [sum(map(operator.mul, q, row)) for row in rows]


# ------------------------------------------------------------------ hybrid search

def _ranked(scores, pool):
    return [number for number, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:pool]]


def reciprocal_rank_fusion(rankings, weights=None, constant=60):
    """rankings: lists of passage numbers, best first. Returns {passage number: fused score}. Rank counts, not raw scores, so a keyword score and a
    cosine never have to be put on one scale."""
    fused = {}
    for position, ranking in enumerate(rankings):
        weight = (weights or [1.0] * len(rankings))[position]
        for rank, number in enumerate(ranking):
            fused[number] = fused.get(number, 0.0) + weight / (constant + rank + 1)
    return fused


def _near_duplicate(a, b, limit=0.9):
    sa, sb = set(tokenize(a)), set(tokenize(b))
    return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= limit


def hybrid_search(query, query_vector, bm25, rows, passages, k=5, pool=50, min_similarity=0.55, min_coverage=0.6, dedupe=True):
    """The best `k` passages for a question.

    query: the question text. query_vector: its embedding, or None (keyword search only). bm25 / rows / passages: the collection's index, vectors
    and passage records, in the same order. Returns {"hits": [...], "confident": bool}. Each hit: {"n", "score", "vector", "bm25", "coverage"} where
    `coverage` is the share of the question's words found in the passage. `confident` is True when the best hit is similar enough (cosine at least
    `min_similarity`) or contains most of the question's words; when it is False the caller should answer "the books do not say" instead of guessing.
    """
    terms = tokenize(query)
    if query_vector is not None and not any(query_vector):
        query_vector = None  # an all-zero vector says nothing and would rank every passage equally
    keyword = bm25_scores(bm25, terms) if terms else {}
    cosine = dict(enumerate(vector_scores(query_vector, rows))) if (query_vector is not None and rows) else {}
    rankings = [r for r in (_ranked(cosine, pool), _ranked(keyword, pool)) if r]
    if not rankings:
        return {"hits": [], "confident": False}
    fused = reciprocal_rank_fusion(rankings, [1.0] * len(rankings))
    order = sorted(fused, key=lambda number: (-fused[number], number))
    wanted = set(terms)
    hits = []
    for number in order:
        if len(hits) >= k:
            break
        passage = passages[number]
        if dedupe and any(_near_duplicate(passage["text"], passages[h["n"]]["text"]) for h in hits):
            continue
        present = set(tokenize(embed_text(passage)))
        hits.append({"n": number, "score": round(fused[number], 6), "vector": round(cosine[number], 4) if number in cosine else None,
                     "bm25": round(keyword[number], 4) if number in keyword else None,
                     "coverage": round(len(wanted & present) / len(wanted), 3) if wanted else 0.0})
    best = hits[0] if hits else None
    best_cosine = max(cosine.values()) if cosine else None
    strong = bool(best) and ((best_cosine is not None and best_cosine >= min_similarity) or best["coverage"] >= min_coverage)
    return {"hits": hits, "confident": strong}


# ------------------------------------------------------------------ evaluation

def hit_matches(passage, expect):
    """expect: {"pages": [12, 13]} (the passage covers one of them) and/or {"contains": "text that must be inside"}; every given rule must hold."""
    if not isinstance(expect, dict) or not (expect.get("pages") or expect.get("contains")):
        return False
    if expect.get("pages") and not any(passage["page"] <= p <= passage["page_end"] for p in expect["pages"]):
        return False
    if expect.get("contains") and expect["contains"].lower() not in passage["text"].lower():
        return False
    return True


def evaluate(cases, search, passages, k=5):
    """cases: [{"question", "expect"}]. search(question) -> {"hits": [...], "confident": bool} (hits as from hybrid_search). Returns recall@k, the mean
    reciprocal rank and one row per question, so a change of chunking, model or thresholds shows up as a number. A case with expect=None is a question
    the books do NOT answer: it counts as right only when the search is not confident."""
    rows, recalled, reciprocal, answerable = [], 0, 0.0, 0
    refused_ok, unanswerable = 0, 0
    for case in cases:
        result = search(case["question"])
        hits = result["hits"][:k]
        if case.get("expect") is None:
            unanswerable += 1
            ok = not result["confident"]
            refused_ok += ok
            rows.append({"question": case["question"], "answerable": False, "ok": ok})
            continue
        answerable += 1
        rank = next((i + 1 for i, h in enumerate(hits) if hit_matches(passages[h["n"]], case["expect"])), 0)
        recalled += bool(rank)
        reciprocal += (1 / rank) if rank else 0.0
        rows.append({"question": case["question"], "answerable": True, "rank": rank, "ok": bool(rank)})
    return {"recall": round(recalled / answerable, 4) if answerable else None, "mrr": round(reciprocal / answerable, 4) if answerable else None,
            "refusal": round(refused_ok / unanswerable, 4) if unanswerable else None, "cases": rows, "k": k}
