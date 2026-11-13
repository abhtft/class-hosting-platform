# Chapter 11: RAG: books, embeddings, keyword + vector search, confidence and evaluation

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Code: `lambda/knowledge_core.py` (pure functions: clean, chunk, BM25, vectors, fusion, evaluation), `knowledge_ingest.py` (PDF → passages), `embeddings.py`, `knowledge_store.py` (S3 layout + warm cache), `knowledge.py` / `knowledge_api.py` (limits + API), `knowledge_worker.py` (book reader), student helpers `student/class_knowledge.py`, `class_quality.py`.
> Read `docs/RAG_GUIDE.md` alongside this chapter.

## A. Concepts

**Q361 [B] What is RAG (Retrieval-Augmented Generation) in one paragraph?**
Instead of asking a model to remember facts, you first **retrieve** the most relevant passages from your own documents and then **generate** an answer using only those passages (and cite them). It reduces hallucination, keeps knowledge up to date without retraining, and lets you show sources.
Study: [RAG paper (Lewis et al., 2020)](https://arxiv.org/abs/2005.11401), [Pinecone: What is RAG?](https://www.pinecone.io/learn/retrieval-augmented-generation/)

**Q362 [B] Walk through the RAG pipeline in this project.**
Ingest: file → extract text per page → clean headers/footers → cut into ~300-word passages → embed each (section title + text) → store passages, packed vectors and a BM25 index. Query: embed question → vector search top 50 + keyword search top 50 → fuse → de-duplicate → keep k → **confidence gate** → if confident, class AI answers *only* from numbered passages and cites `[n]`; else "The books do not say." with no AI call.

**Q363 [B] What is an embedding?**
A list of numbers (here 768) representing the meaning of text, so semantically similar texts have nearby vectors. Same model must embed documents and questions, otherwise the vectors are not comparable.
Study: [Jay Alammar: The Illustrated Word2vec](https://jalammar.github.io/illustrated-word2vec/), [OpenAI: Embeddings guide](https://platform.openai.com/docs/guides/embeddings)

**Q364 [I] What is cosine similarity and why unit-length vectors?**
The cosine of the angle between two vectors (1 = same direction). If vectors are normalised to length 1, cosine equals the dot product, which is fast. The code `normalize`s vectors and packs them as float16 rows.
Study: [Cosine similarity explained](https://www.pinecone.io/learn/vector-similarity/)

**Q365 [I] Why store vectors as float16 and in a packed file instead of a vector database?**
Half the size of float32 with tiny accuracy loss, simple to load into memory, no server to run (idle cost ≈ nothing). Limits (≤60,000 passages per library, 5,000 per student) keep brute-force search fast enough in a Lambda.
Repo: `knowledge_core.pack_vectors`, `knowledge_store.py`.

**Q366 [I] When would brute-force search stop being acceptable and what replaces it?**
Beyond ~hundreds of thousands of vectors per query. Approximate nearest neighbour indexes (HNSW, IVF) in a vector DB (pgvector, OpenSearch, Qdrant, Pinecone...) trade exactness for speed.
Study: [HNSW explained](https://www.pinecone.io/learn/series/faiss/hnsw/), [pgvector](https://github.com/pgvector/pgvector)

**Q367 [B] What is BM25?**
A classic keyword ranking function: scores a passage higher when it contains the query's rarer words more often, with diminishing returns for repetition (`k1=1.5`) and length normalisation (`b=0.75`). It's the engine of Elasticsearch/Lucene by default.
Study: [Elastic: Practical BM25](https://www.elastic.co/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables), [Stanford IR Book ch. 11](https://nlp.stanford.edu/IR-book/)

**Q368 [I] Why combine keyword and vector search ("hybrid")?**
Vectors capture meaning but miss exact identifiers, names, numbers and rare terms; BM25 does the opposite. Each fails where the other succeeds, so fusing them is usually more robust.
Study: [Weaviate: Hybrid search explained](https://weaviate.io/blog/hybrid-search-explained)

**Q369 [I] What is Reciprocal Rank Fusion (RRF)?**
Combine ranked lists using only ranks: `score = Σ weight / (60 + rank + 1)`. Because it ignores raw scores, you don't have to put BM25 values and cosines on one scale. Constant 60 comes from the original paper.
Study: [RRF paper (Cormack et al.)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf), `knowledge_core.reciprocal_rank_fusion`.

**Q370 [I] What are stop words and stemming and why do they matter for BM25?**
Stop words ("the", "of") are dropped as noise; stemming reduces "running/runs" to a root. The implementation here is **English-only** (`STOPWORDS`, `stem`); other languages (e.g. Hindi) need their own, listed under "not built yet".

**Q371 [I] What is "chunking" and what trade-offs does chunk size have?**
Splitting documents into retrievable pieces. Too small → lose context; too big → dilute meaning, waste tokens, retrieve noise. Here ~300 words, 40-word overlap, whole sentences, new passage at each heading, and a page break closes a passage once it has 100 words so citations point to one page.
Study: [Pinecone: Chunking strategies](https://www.pinecone.io/learn/chunking-strategies/), Greg Kamradt "5 Levels of Text Splitting" (YouTube).

**Q372 [I] Why overlap chunks?**
So a fact split across a boundary appears whole in at least one chunk.

**Q373 [I] Why prepend the section title to the text before embedding?**
A mid-chapter paragraph often doesn't repeat the topic; the heading carries it. `embed_text(chunk)` = section + text. Related to Anthropic's "contextual retrieval" idea.
Study: [Anthropic: Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)

**Q374 [I] What does "cleaning" remove, and why only at page edges?**
Repeating headers/footers and page numbers (`12`, `(iv)`, `- 7 -`) in the first/last two lines of pages. A line repeated in the middle of a page might be real content, so it's kept.

**Q375 [I] Why do scanned PDFs fail, and what is OCR?**
No text layer to extract. OCR (optical character recognition) reads pixels. A hook exists with its own cap but isn't switched on; pages with almost no text are reported and skipped.
Study: [Tesseract](https://github.com/tesseract-ocr/tesseract), cloud OCR (AWS Textract).

## B. The confidence gate and evaluation

**Q376 [B] What is the confidence gate and why is it not a score?**
`confident` is true if the best cosine ≥ `min_similarity` (0.55) **or** the best passage contains ≥ `min_coverage` (0.6) of the question's words. If false, `answer()` returns "The books do not say." **without** calling the AI (saves money, avoids guessing).

**Q377 [I] Why not rely on the LLM to say "I don't know"?**
Models often answer anyway. A deterministic gate before generation is cheaper and more reliable, and a refusal rate can be measured.

**Q378 [I] How were 0.55 chosen?**
Calibrated on a school textbook: answerable questions scored ~0.63–0.66 and unrelated ~0.49; 0.55 sits between. Different embedding models and domains produce different distributions, so thresholds must be calibrated per domain.

**Q379 [I] What are recall, MRR and refusal rate?**
Recall@k: is a right passage in the top k? MRR (mean reciprocal rank): average of 1/rank of the first correct hit (higher rank = better). Refusal: does it say "don't know" when it should (and not when it shouldn't)? `knowledge_core.evaluate` and `class_quality.evaluate_retrieval` compute them.
Study: [Evaluation metrics for retrieval](https://weaviate.io/blog/retrieval-evaluation-metrics)

**Q380 [I] What is the "golden set" for a shelf?**
15–30 real questions with the page/phrase that answers each, plus ~10 that the books must NOT answer, including near-misses. Change one thing at a time and re-run.

**Q381 [I] Which direction of error is worse for medical/legal/financial material?**
Answering wrongly. Prefer refusing too often: raise thresholds, always show sources, keep a human in the loop.

**Q382 [D] What is a re-ranker and why is it listed as "not built"?**
A second, more precise model (cross-encoder) re-scores the top candidates against the query, improving precision at extra cost/latency. It's a common next step after hybrid search.
Study: [Sentence-Transformers: cross-encoders](https://www.sbert.net/examples/applications/cross-encoder/README.html)

**Q383 [D] What is "lost in the middle" and how does `k` relate?**
Models use information at the start/end of long contexts better than the middle. More passages (`k`) add recall but dilute attention and cost; typical k=4–6.

**Q384 [I] Why are retrieved passages fenced as DATA?**
Prompt-injection defence: the prompt tells the model to ignore instructions inside passages. It reduces but cannot eliminate the risk.

**Q385 [I] What does "cited or looked-at" in the sources list mean?**
Each source is marked whether the answer actually cited `[n]` it or merely retrieved it, so the UI/teacher can see which passages mattered.

## C. Storage, caching and cost

**Q386 [I] What's the S3 layout of a collection and why is `doc.json` written last?**
`meta.json` (pointer), `docs/<doc>/passages.json.gz`, `vectors.f16`, then `doc.json`; and `index/<version>-<id>/` for the combined index. S3 has no multi-object transactions, so "a document exists once doc.json does" gives atomic visibility without locks.

**Q387 [I] Why version the search index directory?**
Readers keep using the old index while a new one is built; the pointer in `meta.json` flips when ready. Immutable versions + a pointer is a standard way to get atomic updates on object storage.

**Q388 [I] What is the warm cache in `knowledge_store`?**
Module-level cache of loaded shelves (`knowledge.cacheShelves` 6, `cachePassages` 60,000) so repeated searches in a warm Lambda read almost nothing from S3.

**Q389 [I] Why is an embedding cache keyed by content (90 days)?**
Re-adding the same book or paragraph costs nothing; identical text yields identical vectors for a given model.

**Q390 [I] How much does reading a book cost and what guards the spend?**
About $0.15 per million tokens (a 400-page book ≈ 200,000 tokens ≈ a few cents, once). Guards in order: shelf limits (before any spend) → student's monthly allowance → class ceiling `$3` → at most 2 books read at once.

**Q391 [I] Why can the ingestion worker never be retried automatically?**
Retries could double-spend on embeddings and duplicate work. "Book reader function (15 min, never retried)"; stale work is detected and failed with a plain reason and the reservation returned.

**Q392 [I] What happens if a book exceeds the limits mid-processing?**
Fails with a plain message, gives back the reservation and spends **no money** (limits are checked before embedding).

**Q393 [D] Why does a shelf keep the embedding model it was created with?**
Mixed models make vectors incomparable. Changing `KnowledgeEmbeddingModel` applies only to new shelves; to change an existing shelf you must re-embed.

**Q394 [D] Why are no task types sent to embeddings by default?**
Google supports "document" vs "query" task types (better retrieval) but OpenRouter doesn't, and vectors made with different task types aren't comparable. Compatibility beats a small quality gain here.

**Q395 [I] How does the folder sync avoid re-reading unchanged files?**
It compares a content hash per file with what the portal reports; same hash and ready → keep; new → upload; changed → upload new and remove old once ready; missing from the folder → reported (removed with `--prune`).

**Q396 [I] What are the privacy facts a teacher must know?**
Book text and questions go to the AI provider when answering, and passages to the embedding provider when reading. The teacher sees counts/sizes only. Don't add confidential or personal material. Copyright is the user's responsibility; the kit git-ignores `knowledge/`.

## D. Beyond what's built (roadmap thinking)

**Q397 [D] List three quality improvements and the problem each solves.**
Per-shelf settings/presets (thresholds & chunk size per domain), synonyms/jargon lists (abbreviation mismatch), table-aware extraction (tables answer wrongly), re-ranker (precision), query rewriting/multi-query (vague questions), metadata filters (date/type), multilingual BM25 (non-English).

**Q398 [D] What is query rewriting / HyDE?**
Transform the user question (expand, clarify, or generate a hypothetical answer) before retrieval to bridge vocabulary gaps.
Study: [HyDE paper](https://arxiv.org/abs/2212.10496)

**Q399 [D] How would you measure end-to-end answer quality, not just retrieval?**
Faithfulness (is the answer supported by passages?), answer relevance, context precision/recall. Frameworks: Ragas, TruLens, or LLM-as-judge with human spot checks.
Study: [Ragas docs](https://docs.ragas.io/)

**Q400 [D] Why is "quality equals cost" the direction in the project notes?**
Better answers usually need more compute (re-rankers, more passages, bigger models, evals). With a $2–5/month budget, each improvement must be measured against cost; hence measure first, change second.

## Study materials for this chapter

- **Pinecone Learning Center** (embeddings, chunking, hybrid search): <https://www.pinecone.io/learn/>
- **Anthropic: Contextual Retrieval**: <https://www.anthropic.com/news/contextual-retrieval>
- **Stanford IR book (free)**: <https://nlp.stanford.edu/IR-book/> (BM25 chapter)
- **LlamaIndex / LangChain RAG tutorials**: <https://docs.llamaindex.ai/>, <https://python.langchain.com/docs/tutorials/rag/> (to see what frameworks hide; this repo implements the core in plain Python)
- **Hamel Husain: "Your AI Product Needs Evals"**: <https://hamel.dev/blog/posts/evals/>
- **MTEB leaderboard** (embedding models): <https://huggingface.co/spaces/mteb/leaderboard>
- YouTube (search): "RAG from scratch (LangChain)", "Hybrid search BM25 + embeddings", "Karpathy Intro to LLMs".
- Hands-on: run `tests/test_knowledge_core.py`, then modify `min_similarity` in a test fixture and watch which cases flip.
