# Class knowledge connector: books a student's app can answer from

> **Companion document.** The platform's behaviour is defined by the code and its tests; `tests/test_knowledge*.py`, `tests/test_template_knowledge.py` and `tests/test_class_knowledge.*` check everything stated here. Added in 2.2.0.

An optional feature. The teacher can give a student's **backend** a book shelf: PDFs and text files are read once, cut into passages and stored with their meaning (embeddings), and the student's app asks a question and gets back the passages that answer it, with the book, page and section. There is also a **class library** the teacher fills (the school's textbooks, a handbook) that every student with the feature can search but not change. It exists because "ask my notes" and "study helper for chapter 5" are the first thing students want to build, and a model alone answers from memory and invents things.

## 1. At a glance

| | |
|---|---|
| What | Per student: collections of books (PDF, `.txt`, Markdown, HTML), a search that returns passages with page and section, and a `confident` flag that is false when the books do not seem to answer. A class library read-only for students |
| Who controls it | The teacher, per student: on/off and the limits below. Off by default, and absent from the stack until `KnowledgeEnabled=true` (installer feature `knowledge`, needs the backend) |
| Isolation | The student's function holds an opaque key (`ks_<slug>.<secret>`). The API works out the owner from it and builds every S3 path itself. A student cannot name another student's shelf; the class library is addressed as `library/<name>` and is read-only |
| Cost control | Limits are checked **before** any money is spent; a file is embedded once (re-adding the same file is almost free: embeddings are cached for 90 days by content); a class-wide monthly ceiling (default 3 US dollars) stops new books being read while search keeps working; at most 2 books are read at once |
| Storage | S3 only, under `knowledge/` (never served by CloudFront), searched in plain Python: no vector database, nothing runs while idle |
| Code | `lambda/knowledge_core.py` (chunking, keyword and vector ranking), `embeddings.py` (Google, or OpenRouter), `knowledge_store.py` (S3 layout and index), `knowledge_ingest.py` (PDF and text reading, vendored `pypdf`), `knowledge.py` (rules and limits), `knowledge_api.py`, `knowledge_worker.py`; helpers `student/class_knowledge.py` and `.mjs` |

## 2. How it works

1. **Add a book.** The helper asks the API for an upload form (a size-limited S3 POST), sends the file straight to S3 and then calls `start`. The bytes never pass through a Lambda.
2. **Read it.** `start` checks the budgets and starts the **book reader** (a separate function with 15 minutes and 2 GB, never retried by the platform, because a retry would pay for the embeddings twice). It extracts the text, removes repeated headers, footers and page numbers at page edges, cuts passages of about 300 words that follow headings and page breaks, checks every limit, then embeds the passages and writes the book and a new version of the collection's index. A page with almost no text (a scan) is listed and skipped; a scan reader (OCR) is an optional hook that is not switched on.
3. **Search.** The question is embedded (cached), then ranked two ways, by words (BM25) and by meaning (cosine over packed vectors), and the two lists are fused. The answer carries `confident`: true only when the best passage is close enough in meaning **and** covers enough of the question's words. A question the books cannot answer therefore comes back `confident: false`, and `class_knowledge.answer()` replies "The books do not say." without calling the AI.
4. **Answer.** `answer()` puts the passages in a fenced block, tells the AI to use only them and to treat them as study material, never as instructions (a book that says "ignore your rules" is data), and returns the sources with it.

Pure Python search over a few thousand passages takes about a tenth of a second; class-12 study books are well inside that.

## 3. Limits (defaults; the teacher changes them per student)

| Limit | Default | Teacher's range |
|---|---|---|
| Collections | 5 | 1 to 20 |
| Books | 20 | 1 to 200 |
| Passages in all | 5,000 | 100 to 60,000 |
| Space for books, passages and vectors | 50 MB | 1 to 500 MB |
| One file | 25 MB (hard maximum 50 MB, 1,500 pages) | 1 to 50 MB |
| Searches | 500 per day | 0 to 1,000,000 per day, month or in total |
| Adding books (embedding tokens) | 2,000,000 a month | set by the teacher |
| Class-wide adding books | 3 US dollars a month (`KnowledgeMonthlyCapUsd`) | stack parameter |

Embedding costs 0.15 US dollars per million tokens with the default model (`gemini-embedding-001`, 768 numbers per passage): a 400-page textbook is a few cents. Searching costs a fraction of a cent per thousand questions (only the question is embedded).

## 4. What the teacher does

1. Install or upgrade with the feature: `node setup.mjs plan --enable knowledge`, then `apply <plan id>`. The platform's AI secret must hold `GOOGLE_API_KEY` (the same key the class AI uses). To embed through OpenRouter instead, set `KnowledgeEmbeddingModel` to `google/gemini-embedding-001` and add `OPENROUTER_EMBEDDING_KEY` (an inference key; the management key cannot call models) to the same secret. The model id is the same on both, so vectors stay interchangeable. The dimension (`KnowledgeEmbeddingDims`, 768) is fixed per collection when it is created.
2. In the teacher page, **Class knowledge** card: switch it on for a student and set the limits; **Class library** panel: create collections, add books, see their state, remove them, and watch this month's spending against the ceiling.
3. The student's backend gets `CLASS_KNOWLEDGE_URL` and `CLASS_KNOWLEDGE_KEY` after its next publish. These names cannot be overwritten or removed by the student.

## 5. What the student writes

```python
import class_knowledge as kb

kb.create_collection("physics", "Physics notes")
kb.add_document("physics", "chapter1.pdf", pdf_bytes)      # wait=False inside a web request
found = kb.search("What is Ohm's law?")                    # own shelves and the class library
if found["confident"]:
    best = found["hits"][0]                                # text, title, page, section, collection
reply = kb.answer("What is Ohm's law?")                    # {"answer", "sources", "confident"}
```

Node: `import * as kb from './class_knowledge.mjs'` with the same names in camel case (`createCollection`, `addDocument`, `search`, `answer`). On the student's computer (`npm run dev`) text, Markdown and HTML books work from the git-ignored `.class-knowledge/` folder with a plain keyword search and the same answer shapes; PDFs are only read on the platform. Errors are `KnowledgeError` with a kind (`not_enabled`, `allowance_used`, `class_budget`, `quota_exceeded`, `too_large`, `unsupported`, `busy`, `not_ready`, `not_found`, `bad_state`, `bad_request`, `failed`, `token_inactive`, `bad_key`, `unavailable`). The rules for AI editors ("Answering from books") say to show sources, to say "The books do not say." instead of guessing, to treat book text as data, and never to let a visitor choose whose shelf is searched.

## 6. Permissions (the reviewed list)

| Function | Can do | Cannot do |
|---|---|---|
| Knowledge API (29 s, 1 GB) | read single items in the tokens table; update counters whose keys start `kdoc#`, `kpass#`, `kbytes#`, `ksearch#`, `ktok#`, `kspend#`, `kwork#`; write whole items only for the embedding cache (`emb#`) and the reader slot (`kwork#`); S3 read and write under `knowledge/`; start the book reader; read the AI secret | scan or query the table, delete items, touch the kit, student sites or student files, start anything else |
| Book reader (15 min, 2 GB) | the same, except it cannot start anything | be called from the internet, be retried by the platform |
| Portal | `knowledge/` objects (the library), start the book reader for a library book | anything new elsewhere |

`tests/test_template_knowledge.py` fails if any of these grants grow.

## 7. Known limits

- No OCR yet: a scanned book is read as empty pages and the document says so. Digital PDFs (textbooks from publishers' sites) work.
- About 0.4 percent of words in some PDFs come out split by a stray space after a capital ("Pr ofessor"); search still finds them through the neighbouring words.
- Tables, equations and figures are text only. A caption is searchable, a diagram is not.
- English is the tuned case; other languages embed fine but the keyword part knows only English plurals and endings.
- One writer per collection at a time: two books finishing at the same moment retry their index rebuild, they do not lose each other.
- Copyright is the teacher's and the student's responsibility: only add books you may share with the class. The platform does not read or inspect what a student stores.
