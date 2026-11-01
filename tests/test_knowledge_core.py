"""The knowledge engine (lambda/knowledge_core.py): the parts that decide retrieval QUALITY, tested on behaviour with a small synthetic study book.

No AWS and no network. Vectors here come from a hashing "embedder" (bag of words into a few hundred numbers): it is lexical, so it checks the plumbing
and the fusion logic, not semantic quality. Semantic quality is measured later on real books with a real embedding model (evaluate()).
"""
import os
import random
import sys
import time
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))
import knowledge_core as kc  # noqa: E402

DIMS = 256


def fake_embed(text):
    row = [0.0] * DIMS
    for term in kc.tokenize(text):
        row[zlib.crc32(term.encode()) % DIMS] += 1.0
    return row


# ---------------------------------------------------------------- words
assert kc.tokenize("The plants are making Food, using sunlight in 1857!") == ["plant", "mak", "food", "using", "sunlight", "1857"]
assert kc.tokenize("the and of") == [] and kc.tokenize("the and of", keep_stopwords=True) == ["the", "and", "of"]
assert "1857" in kc.tokenize("In 1857 the revolt began") and kc.tokenize("a b c") == ["b", "c"]
assert kc.tokenize("Plants plant") == ["plant", "plant"], "plural and singular meet in the index"
assert kc.tokenize("cities city") == ["city", "city"] and kc.tokenize("gas gases") == ["gas", "gas"], "plurals meet, short words are left alone"
assert kc.tokenize("make makes making") == ["mak", "mak", "mak"], "a final e and the endings go, so verb forms meet"
assert kc.tokenize("") == [] and kc.tokenize("!!! ???") == []
assert kc.estimate_tokens("abcd" * 10) == 10
print("  ok  tokenizer: lowercase words and numbers, stopwords dropped, plural and verb forms meet, short words untouched")

# ---------------------------------------------------------------- cleaning
header = "NCERT Science Class 9"
parts = ["roots", "stems", "leaves", "flowers", "fruits", "seeds", "soil", "rain"]
pages = []
for number in range(1, 9):
    pages.append((number, f"{header}\nPage {number}\nPlants make food by photo-\nsynthesis in their {parts[number - 1]}.\nSolution:\nSee the {parts[number - 1]} diagram.\nThis page discusses {parts[number - 1]} in detail.\n{number}"))
cleaned = dict(kc.clean_pages(pages))
assert all(header not in text for text in cleaned.values()), "the running header is removed"
assert all(not any(line.strip() in (str(n), f"Page {n}") for line in cleaned[n].split("\n")) for n in cleaned), "page-number lines are removed"
assert all("photosynthesis in their" in cleaned[n] for n in cleaned), "a hyphenated line break is joined"
assert all(f"This page discusses {parts[n - 1]} in detail." in cleaned[n] for n in cleaned), "real text at the bottom of a page survives"
assert all("Solution:" in cleaned[n] for n in cleaned), "a label that repeats in the MIDDLE of pages is real text and stays"
for number_line in ["12", "Page 12", "12 of 40", "(iv)", "[12]", "- 5 -", "– 7 –", "xiv", "(xii)", "IV"]:
    assert kc._PAGE_NUMBER_LINE.match(number_line), number_line
for word_line in ["Civil", "civil", "Mix", "Dill", "vivid", "Chapter 3", "2026-27", "The 12 apostles", "mild", ""]:
    assert not kc._PAGE_NUMBER_LINE.match(word_line), word_line
edge_pages = [(n, f"Civil {['law', 'war', 'rights', 'service'][n - 1]}\nBody text of page number {n} goes here with some words.\nMore words follow here to fill space on page {n}.\n({['iv', 'v', 'vi', 'vii'][n - 1]})") for n in range(1, 5)]
edge_clean = dict(kc.clean_pages(edge_pages))
assert all(not text.strip().endswith(")") for text in edge_clean.values()) and all(text.startswith("Civil ") for text in edge_clean.values()), "roman page numbers go, a first line starting with 'Civil' stays"
one_off = kc.clean_pages([(1, "Unit test review\nBody."), (2, "Other text"), (3, "More text"), (4, "Even more"), (5, "Last")])
assert "Unit test review" in dict(one_off)[1], "a short line seen on one page only is not a header"
print("  ok  cleaning: running headers and page numbers at the page edges go; hyphenated breaks joined; middle-of-page labels and real text kept")

# ---------------------------------------------------------------- headings
for line in ["Chapter 3: Motion", "CHAPTER IV", "3.1 Newton's Laws", "Unit 2 Force and pressure", "NEWTON'S LAWS OF MOTION", "Lesson 12"]:
    assert kc.is_heading(line), line
for line in ["The water cycle describes how water moves.", "3. Find the value of x.", "1857", "Q1. What is a quadratic equation?", "", "x " * 40, "Plants make food using sunlight, water and air"]:
    assert not kc.is_heading(line), line
print("  ok  headings: chapter, unit, numbered and all-capitals lines yes; sentences, questions and numbers no")

# ---------------------------------------------------------------- chunking
S = lambda n, word: " ".join([f"{word.capitalize() if i == 0 else word}{i}" for i in range(n)]) + "."  # a sentence of n words, starting with a capital
sentences = [S(10, f"alpha{k}x") for k in range(8)]
book = [(1, "Chapter 1: First topic\n" + " ".join(sentences[:4])), (2, " ".join(sentences[4:8]) + "\n\nChapter 2: Second topic\n" + S(12, "beta") + " " + S(12, "gamma") + " " + S(12, "delta"))]
chunks = kc.chunk_pages(book, target_words=50, overlap_words=15, min_words=15)
assert [c["n"] for c in chunks] == list(range(len(chunks))), "numbered in reading order"
assert all(c["words"] <= 80 for c in chunks), [c["words"] for c in chunks]
assert not any(c["text"].startswith(("Chapter", "CHAPTER")) for c in chunks), "a heading is a section name, not passage text"
first_section = [c for c in chunks if c["section"] == "Chapter 1: First topic"]
second_section = [c for c in chunks if c["section"] == "Chapter 2: Second topic"]
assert first_section and second_section and len(first_section) + len(second_section) == len(chunks)
assert all("beta" not in c["text"] and "gamma" not in c["text"] for c in first_section), "a new section never shares a passage with the one before"
assert all("alpha" not in c["text"] for c in second_section), "and no overlap crosses a section"
body = " ".join(c["text"] for c in first_section)
assert all(s.split()[0] in body for s in sentences), "no sentence is lost"
assert len(first_section) >= 2 and any(word in first_section[1]["text"] for word in first_section[0]["text"].split()[-10:]), "overlap carries the end of one passage into the next"
assert first_section[0]["page"] == 1 and first_section[-1]["page_end"] == 2 and any(c["page"] < c["page_end"] for c in first_section) or first_section[-1]["page_end"] == 2
print("  ok  chunker: sentences kept whole, sections never mixed, no sentence lost, overlap within a section, pages tracked")

# a page break ends a passage when the page finished a sentence and the passage is already long (found on a real textbook: an imprint page was joined to the foreword)
page_a = " ".join(S(25, f"a{k}x") for k in range(6))   # 150 words, finished sentences
page_b = " ".join(S(25, f"b{k}x") for k in range(6))
broken = kc.chunk_pages([(1, page_a), (2, page_b)])
assert [(c["page"], c["page_end"]) for c in broken] == [(1, 1), (2, 2)], [(c["page"], c["page_end"]) for c in broken]
assert not broken[1]["text"].startswith(broken[0]["text"].split(". ")[-2]), "no overlap is carried across a page break"
running = kc.chunk_pages([(1, page_a[:-1] + " and the sentence goes on"), (2, "to the next page and ends here. " + page_b)])
assert running[0]["page"] == 1 and running[0]["page_end"] == 2, "a sentence still running at the end of a page is not cut at the page break"
short = kc.chunk_pages([(1, " ".join(S(25, f"c{k}x") for k in range(2))), (2, page_b)])
assert short[0]["page"] == 1 and short[0]["page_end"] == 2, "a short page (under 100 words) joins the next one, as before"
assert [(c["page"], c["page_end"]) for c in kc.chunk_pages([(1, page_a), (2, page_b)], page_break_words=10 ** 6)] == [(1, 2)], "with the rule off the two pages fit one passage, as before"

# an extractor that knows a line is a heading (markdown, HTML) says so; the line need not look like one
marked = kc.chunk_pages([(1, f"{kc.HEADING_MARK}How plants breathe\nStomata open in the day. They close at night.\n\n{kc.HEADING_MARK}\nPlain text after an empty heading.")], target_words=50, overlap_words=0, min_words=3)
assert marked[0]["section"] == "How plants breathe" and "How plants breathe" not in marked[0]["text"] and "Plain text after an empty heading." in " ".join(c["text"] for c in marked)

# a too-short tail is folded into the passage before it
tail_book = [(1, "Chapter 1: Only\n" + " ".join(S(10, f"w{k}x") for k in range(6)))]
folded = kc.chunk_pages(tail_book, target_words=50, overlap_words=15, min_words=15)
assert len(folded) == 1 and folded[0]["words"] == 60, [c["words"] for c in folded]
# no heading and nothing to chunk
assert kc.chunk_pages([]) == [] and kc.chunk_pages([(1, "   \n  ")]) == []
# one very long sentence is cut at word boundaries, nothing exceeds the target
long = kc.chunk_pages([(1, " ".join(["word"] * 500) + ".")], target_words=100, overlap_words=0, min_words=10)
assert len(long) == 5 and all(c["words"] <= 100 for c in long), [c["words"] for c in long]
assert kc.embed_text({"section": "Chapter 1", "text": "Body"}) == "Chapter 1\nBody" and kc.embed_text({"section": "", "text": "Body"}) == "Body"
print("  ok  chunker edges: tail folded, empty input, a 500-word sentence cut, section title in front of the embedded text")

# ---------------------------------------------------------------- BM25
docs = ["Photosynthesis uses sunlight water and carbon dioxide", "The stomata open and close to let gases in and out of the leaf",
        "Akbar was a Mughal emperor who ruled from Agra", "Aurangzeb was the last of the great Mughal emperors"]
index = kc.build_bm25([kc.tokenize(d) for d in docs])
assert index["n"] == 4 and kc.build_bm25([]) == {"n": 0, "avgdl": 0.0, "dl": [], "postings": {}}
scores = kc.bm25_scores(index, kc.tokenize("stomata"))
assert list(scores) == [1], scores
scores = kc.bm25_scores(index, kc.tokenize("Mughal Aurangzeb"))
assert max(scores, key=scores.get) == 3, "the rare word decides, not the common one"
assert kc.bm25_scores(index, []) == {} and kc.bm25_scores(index, ["nonexistent"]) == {}
short = kc.build_bm25([["rock"], ["rock"] + ["filler"] * 30])
s = kc.bm25_scores(short, ["rock"])
assert s[0] > s[1], "the same word counts for more in a short passage"
print("  ok  BM25: rare words decide, absent words score nothing, short passages beat padded ones")

# ---------------------------------------------------------------- vectors
random.seed(7)
rows = [[random.uniform(-1, 1) for _ in range(DIMS)] for _ in range(5)]
packed = kc.pack_vectors(rows)
assert len(packed) == 5 * DIMS * 2
back = kc.unpack_vectors(packed, DIMS)
for original, restored in zip(rows, back):
    unit = kc.normalize(original)
    assert max(abs(a - b) for a, b in zip(unit, restored)) < 2e-3
assert kc.pack_vectors([]) == b""
for bad in (lambda: kc.pack_vectors([[1, 2], [1]]), lambda: kc.unpack_vectors(b"\x00\x00\x00", 2), lambda: kc.unpack_vectors(b"", 0)):
    try:
        bad()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
assert kc.normalize([0, 0, 0]) == [0.0, 0.0, 0.0]
sims = kc.vector_scores(rows[0], back)
assert sims[0] > 0.999 and all(abs(x) < 0.4 for x in sims[1:]), sims
compact = kc.unpack_vectors(packed, DIMS, compact=True)
assert all(type(r).__name__ == "array" and r.typecode == "f" for r in compact), "compact rows are 4-byte arrays, not tuples of Python floats"
assert max(abs(a - b) for a, b in zip(kc.vector_scores(rows[0], compact), sims)) < 1e-6, "the same scores from compact rows"
print("  ok  vectors: float16 round trip within 0.002, mismatches refused, cosine of a vector with itself is 1")

# ---------------------------------------------------------------- fusion
fused = kc.reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]])
assert max(fused, key=fused.get) == 1 and fused[3] > fused[2] and fused[4] < fused[2] + 1, fused
assert kc.reciprocal_rank_fusion([]) == {}
print("  ok  rank fusion: agreement of two rankings wins over one good rank")

# ---------------------------------------------------------------- hybrid search over a synthetic study book
TOPICS = {
    "Chapter 1: Photosynthesis": [
        "Photosynthesis is the process by which green plants make their own food. The leaves absorb sunlight using chlorophyll. Water from the soil and carbon dioxide from the air are combined. Glucose and oxygen are produced.",
        "The stomata are tiny pores on the surface of a leaf. They open and close to let gases pass in and out. Guard cells control the opening of each stomata pore.",
    ],
    "Chapter 2: Laws of Motion": [
        "Newton's first law of motion says that an object stays at rest or moves with constant velocity unless a force acts on it. This property is called inertia.",
        "Newton's second law states that force equals mass multiplied by acceleration. A heavier object needs a larger force to get the same acceleration.",
    ],
    "Chapter 3: The Mughal Empire": [
        "Akbar was the greatest of the Mughal emperors. He ruled from Agra and built Fatehpur Sikri. Akbar followed a policy of religious tolerance.",
        "Aurangzeb ruled the Mughal empire for nearly fifty years until 1707. After his death the empire weakened and regional powers grew.",
    ],
    "Chapter 4: Quadratic Equations": [
        "A quadratic equation has the form ax2 plus bx plus c equals zero. The discriminant b2 minus 4ac tells how many real roots the equation has.",
    ],
    "Chapter 5: The Water Cycle": [
        "Water evaporates from oceans and rivers when the sun heats it. The vapour rises and condenses into clouds. Rain and snow bring the water back to the ground. This continuous movement is the water cycle.",
    ],
}
page_no, book_pages, truth = 1, [], {}
for title, paragraphs in TOPICS.items():
    for paragraph in paragraphs:
        book_pages.append((page_no, f"NCERT Study Book Class 9\n{page_no}\n{title}\n{paragraph}"))
        truth[page_no] = title
        page_no += 1
book_pages.append((page_no, "NCERT Study Book Class 9\n" + str(page_no) + "\nBack matter of no importance."))
cleaned_pages = kc.clean_pages(book_pages)
passages = kc.chunk_pages(cleaned_pages, target_words=60, overlap_words=10, min_words=15)
assert {p["section"] for p in passages} >= set(TOPICS), "every chapter became a section"
index = kc.build_bm25([kc.tokenize(kc.embed_text(p)) for p in passages])
rows = kc.unpack_vectors(kc.pack_vectors([fake_embed(kc.embed_text(p)) for p in passages]), DIMS)


def search(question, **kw):
    return kc.hybrid_search(question, fake_embed(question), index, rows, passages, **kw)


for question, expected_title in [("How do plants make their own food?", "Chapter 1: Photosynthesis"), ("What do stomata do?", "Chapter 1: Photosynthesis"),
                                 ("State Newton's second law", "Chapter 2: Laws of Motion"), ("What is inertia?", "Chapter 2: Laws of Motion"),
                                 ("Who ruled until 1707?", "Chapter 3: The Mughal Empire"), ("Who built Fatehpur Sikri?", "Chapter 3: The Mughal Empire"),
                                 ("What is the discriminant of a quadratic equation?", "Chapter 4: Quadratic Equations"), ("Explain the water cycle", "Chapter 5: The Water Cycle")]:
    result = search(question, k=3)
    assert result["confident"], question
    assert result["hits"][0]["n"] is not None and passages[result["hits"][0]["n"]]["section"] == expected_title, (question, passages[result["hits"][0]["n"]]["section"])
for question in ["What is on the school canteen lunch menu?", "Who won the football world cup in 2018?", "zzzz qqqq"]:
    assert search(question)["confident"] is False, question
assert search("")["hits"] == [] and search("anything", k=0)["hits"] == []
assert kc.hybrid_search("stomata", None, index, rows, passages)["hits"][0]["bm25"] is not None, "keyword search works without a vector"
assert kc.hybrid_search("stomata", fake_embed("stomata"), kc.build_bm25([]), [], [])["hits"] == []
print("  ok  hybrid search on the synthetic book: right chapter first for 8 questions, off-topic questions are not confident")

# a keyword hit rescues a passage the vectors rank second (fusion by rank)
tiny_passages = [{"text": "alpha topic about gardens", "section": "", "page": 1, "page_end": 1}, {"text": "beta topic about the zebra crossing", "section": "", "page": 2, "page_end": 2}]
tiny_index = kc.build_bm25([kc.tokenize(p["text"]) for p in tiny_passages])
tiny_rows = kc.unpack_vectors(kc.pack_vectors([[1.0, 0.0], [0.9, 0.436]]), 2)
fusion = kc.hybrid_search("zebra", [1.0, 0.05], tiny_index, tiny_rows, tiny_passages, k=2)
assert fusion["hits"][0]["n"] == 1, "the passage with the exact keyword wins although its vector is second"
print("  ok  fusion: an exact keyword beats a slightly closer vector")

# near-duplicate passages are not returned twice
dup_passages = [{"text": "the water cycle moves water around the planet", "section": "", "page": 1, "page_end": 1}] * 2 + [{"text": "newton described the force of gravity", "section": "", "page": 3, "page_end": 3}]
dup_index = kc.build_bm25([kc.tokenize(p["text"]) for p in dup_passages])
dup_rows = kc.unpack_vectors(kc.pack_vectors([fake_embed(p["text"]) for p in dup_passages]), DIMS)
twice = kc.hybrid_search("water cycle", fake_embed("water cycle"), dup_index, dup_rows, dup_passages, k=3)
assert [h["n"] for h in twice["hits"]].count(0) + [h["n"] for h in twice["hits"]].count(1) == 1
assert len(kc.hybrid_search("water cycle", fake_embed("water cycle"), dup_index, dup_rows, dup_passages, k=3, dedupe=False)["hits"]) == 3
print("  ok  duplicates: the same passage is not returned twice")

# ---------------------------------------------------------------- evaluation
cases = [{"question": q, "expect": {"pages": [p]}} for q, p in [("How do plants make their own food?", 1), ("What do stomata do?", 2), ("State Newton's second law", 4),
                                                                ("Who ruled until 1707?", 6), ("Explain the water cycle", 8)]]
cases += [{"question": "Who built Fatehpur Sikri?", "expect": {"contains": "fatehpur sikri"}}, {"question": "What is on the lunch menu?", "expect": None}]
report = kc.evaluate(cases, search, passages, k=3)
assert report["recall"] == 1.0 and report["mrr"] > 0.9 and report["refusal"] == 1.0, report
assert all(row["ok"] for row in report["cases"])
bad_cases = kc.evaluate([{"question": "How do plants make their own food?", "expect": {"pages": [8]}}], search, passages, k=1)
assert bad_cases["recall"] == 0.0 and bad_cases["mrr"] == 0.0 and bad_cases["cases"][0]["rank"] == 0
overconfident = kc.evaluate([{"question": "Explain the water cycle", "expect": None}], search, passages, k=3)
assert overconfident["refusal"] == 0.0, "a question the books answer is not a refusal case"
assert kc.evaluate([], search, passages)["recall"] is None
assert kc.hit_matches({"page": 5, "page_end": 6, "text": "Hello World"}, {"pages": [6], "contains": "hello"}) and not kc.hit_matches({"page": 5, "page_end": 6, "text": "x"}, {"pages": [7]})
assert not kc.hit_matches({"page": 1, "page_end": 1, "text": "x"}, {}) and not kc.hit_matches({"page": 1, "page_end": 1, "text": "x"}, None)
print("  ok  evaluation: recall, mean reciprocal rank and refusal rate computed; a wrong expectation scores zero")

# ---------------------------------------------------------------- speed: a big book must stay well under a Lambda timeout
random.seed(1)
count, dims = 3000, 768
big_rows = kc.unpack_vectors(kc.pack_vectors([[random.gauss(0, 1) for _ in range(dims)] for _ in range(count)]), dims, compact=True)  # the form the service keeps in memory
big_passages = [{"text": f"passage number {i} about topic {i % 40}", "section": "", "page": i // 4 + 1, "page_end": i // 4 + 1} for i in range(count)]
big_index = kc.build_bm25([kc.tokenize(p["text"]) for p in big_passages])
started = time.time()
found = kc.hybrid_search("topic 7 passage", [random.gauss(0, 1) for _ in range(dims)], big_index, big_rows, big_passages, k=5)
elapsed = time.time() - started
assert len(found["hits"]) == 5 and elapsed < 4.0, elapsed
print(f"  ok  speed: {count} passages x {dims} numbers searched in {elapsed:.2f} s (pure Python)")

print("test_knowledge_core.py: all passed")
