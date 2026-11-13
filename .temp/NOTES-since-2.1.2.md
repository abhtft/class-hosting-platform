# Notes: everything built after release 2.1.2 (commit 90679e4)

Written 2026-11-03 for the owner. Covers 18 commits (fbecf39 to 551d93a, 145 files). Nothing here is tagged: the version in the repo is **2.2.0, unreleased**.
Private file (`.temp/` is git-ignored).

---

## 1. Where things stand right now

| Item | Live on your platform? | Notes |
|---|---|---|
| Platform: class knowledge service (API, book reader, teacher card, Library tab) | **Yes** (applied plan 60c6c70e..., then 1191bdf2...) | Off for every student until you switch it on |
| Folder sync routes (`/api/student/knowledge`) | **Yes** (plan 1191bdf2...) | Code-only update, no new AWS permissions |
| Kit: `class_knowledge`, `class_agent`, `class_quality`, `PATTERNS.md`, starter page parts, `npm run knowledge`, `npm run eval` | **Yes, published** up to commit f6ddc3c | New projects get them; existing projects get managed files by kit sync |
| Latest fix 551d93a (search drops duplicate passages) | **No, not published** | Needs your "publish" |
| `verify` after all this | **Not run by me** (needs your teacher password) | Please run it |
| Release tag 2.2.0 | **No** | Only on your explicit "release 2.2.0" |

Test student used for every live test: `test_claude` (folder `D:\Github_Codes\claude_test`). It has extra test files and temporary routes (listed in section 8).

---

## 2. What was built (by area)

### A. Class knowledge service (RAG): books a student's app answers from
Idea: PDFs and text files are read once, cut into passages, stored with their meaning; the app asks a question and gets the best passages with book, page and section, plus a flag that says when the books do NOT answer.

- **Engine (stages 1 to 4):** cleaning of repeated headers/footers/page numbers, passages of ~300 words following headings and page breaks, keyword (BM25) plus meaning (vector) search fused together, a `confident` flag, evaluation numbers (recall, MRR, refusal). PDF reading with a vendored `pypdf` (checked by hash). Embeddings through Google (`gemini-embedding-001`, 768 numbers) or OpenRouter with the same model id. Everything stored in S3, searched in plain Python: no vector database, nothing runs while idle.
- **Connector (stage 5):** teacher switch per student with limits (shelves, books, passages, space, file size, searches, monthly allowance), a private key the student never sees, an API function and a separate book-reader function (15 min, never retried by the platform), counters, a class-wide monthly spending ceiling (default $3), at most 2 books read at once, narrow permissions guarded by tests, an alarm, installer feature `knowledge`.
- **Teacher page:** a Class knowledge card per student (switch, usage, limits, remove all books) and a **Library tab** (class library: create shelves, upload books, see states, remove, spend vs ceiling).
- **Class library:** books you add; every student with the switch on can search them (as `library/<shelf>`), read-only.
- **Student helper `class_knowledge` (Python and Node):** `search`, `answer` (with numbered sources and "The books do not say."), add/delete books, offline keyword search in `npm run dev`.

### B. AI patterns: agents and quality (kit files only, no AWS change)
- **`class_agent`:** a small tool-using agent. Budgets (steps, seconds, tokens), guardrails (only declared tools, checked arguments, no repeated call, max 3 uses per tool, results fenced as data), a trace of every step, human approval for risky tools (run stops, state is plain JSON, `resume` continues with a fresh budget), one retry on provider hiccups, scripted `mock=[...]` for offline use.
- **`class_quality`:** answer cache (key includes the passages found and the answer shape), thumbs feedback, `evaluate` (tests for the app), `evaluate_retrieval` (does the search find the right page), optional AI judge.
- **`PATTERNS.md`:** which pattern to use (plain call, books, router, agent, background job, human approval, quality loop) with skeletons and a checklist. Rules for AI editors and the interview now point to it.
- **Starter examples:** `backend/study.py` (and Node `study.mjs`) with routes `/study`, `/feedback`, `/agent`, `/agent/approve`; `evals/study.json`; `npm run eval`.
- **Starter page:** Study panel (answer, numbered sources, thumbs) and Agent panel with an **approval card** (exact text, what it read, Approve and Decline) and a readable trace.

### C. Folder sync: `knowledge/<shelf>/` and `npm run knowledge`
- A student puts books in `knowledge/<shelf>/`; `npm run knowledge` (also run after `npm run deploy`, non-blocking, `--no-knowledge` skips) uploads and reads them.
- New files uploaded, unchanged skipped by content hash (nothing paid twice), changed file replaces the book of the same name once the new one is ready, **nothing deleted unless `--prune`**, failed book only retried with `--retry`, `--dry-run`, `status`, `--wait`.
- Uses the student's login with the portal (new routes `/api/student/knowledge/...`); the shelf key never leaves the student's function; no route can name another student's shelf or the class library; same limits as the API.
- Kit `.gitignore` ignores `/knowledge/`; `npm run check` warns if books are tracked by git; offline `npm run dev` also searches the text books of that folder.

### D. Other
- **Installer bug fix:** the template grew past 51,200 bytes, so `aws cloudformation deploy` needed the staging bucket; fixed and tested (every future upgrade would have hit it).
- **Version:** repo/kit/editor package moved 2.1.2 to 2.2.0 (unreleased). Kit changes are tracked by hash in `tests/kit-versions.json`.
- Test suite grew from 39 to 43 steps; docs added (`docs/KNOWLEDGE_CONNECTOR.md`, API reference, student kit guide, spec section 4.17 and feature row F17/F18, changelog).
- (A background-jobs architecture doc, commit f2efaca, came from another session.)

---

## 3. Directions I took, and why
1. **Serverless and idle-free**: no vector database, no EC2, no SaaS; cost stays near zero when unused, in line with your $15/month limit.
2. **Cost is checked before it is spent** (limits, cache by content, class-wide ceiling, reader slots), because quality and cost both matter.
3. **Honest answers over fluent ones**: "The books do not say." instead of guessing; sources always shown; book text treated as data, never as instructions.
4. **Kit-first for patterns**: agents and quality tools are helper files (no AWS change), so they ship by kit sync and are testable offline.
5. **Safety first for anything that changes something**: agent actions need a person's click; sync never deletes by itself; books stay out of git.
6. **Real-world tests drive the fixes**: each step was run on `test_claude` with the real AI and your real PDF, and the findings (section 5) changed the code.

---

## 4. Decisions you gave, and decisions still open

**You decided**
- Build the core ourselves (no EC2 / SaaS vector DB); Gemini for embeddings now, OpenRouter-compatible; private student collections stay in your AWS account; teacher-curated class library.
- Focus on quality tools and agent patterns; RAG tuning kept as a pending task.
- Folder sync: folder named `knowledge/`; never delete automatically (`--prune`); git-ignore the folder; sync on every deploy automatically; the editor (MCP) tool later.
- Publish and apply each step (you approved every plan and every publish).

**Still open (I need your answers)**
1. Who may add to the class library (just you, as now?) and is using NCERT PDFs for your own class acceptable to you?
2. Retention of students' own books: delete when a token expires, or keep?
3. Monthly ceiling $3 and per-student allowance (2M embedding tokens): keep or change?
4. Chat model for answers: keep the platform's Gemini flash-lite or change?
5. Should the sync tool warn when an existing project's `.gitignore` lacks `/knowledge/` (existing projects do not get that line automatically)? I recommend yes.
6. Next priority: RAG tuning (more real chapters, thresholds), scan reading (OCR), quality dashboard on the teacher page, or the MCP `sync_knowledge` tool.

---

## 5. Problems found by real tests (and fixed)
- Gemini returns **nothing** (malformed function call) when tools are listed like `name(arg: kind)`; tools are now listed as the exact JSON line, and the same goals pass.
- Gemini read "[needs a human's approval first]" as "I may not use this tool"; wording changed.
- The model searched the same tool repeatedly (cap: 3 per tool); a resumed run had no budget left (now a fresh budget).
- The model once saved a **wrong fact** ("J.V. Narlikar" or both names as chief advisor). Page 9 says Narlikar is Chairman and Ghosh is Chief Advisor. This is why the approval card shows the exact text.
- The live eval caught a retrieval gap: "What is the foreword of the textbook about?" is wrongly refused (not fixed yet).
- Kit sync silently put the old `class_ai.py` back, which proved the new kit must be published as a set; `class_agent` now tolerates an older `class_ai`.
- The answer cache served a copy written by an older helper (fixed: keyed by answer shape).
- Citation numbers did not match the sources list (fixed: numbered, with cited/not cited).
- The same book on two shelves filled the top results with duplicates and made a correct answer come back as "The books do not say." (fixed in 551d93a, **not published yet**).
- Template size limit in the installer (fixed).
- A provider slowness: the class AI timed out for a while; not our code.

---

## 6. What you have NOT tested yet (please check, in this order)
1. `node setup.mjs verify --profile default` (teacher password).
2. Teacher page: **Library** tab (shelf `science`, 1 book, 22 passages), then a student's **Class knowledge** card (switch on/off, edit limits).
3. `test_claude` site: section **3. Ask the books** (a real question, a question the books cannot answer, thumbs) and section **4. An agent that asks first** (approve, then decline).
4. A brand-new student: create a token, switch on Backend, Class AI, Class storage, Class knowledge, download the kit, `npm run check`, `npm run deploy`, add a book in `knowledge/<shelf>/`, `npm run knowledge`.
5. Editor tools in Antigravity with the corrected MCP config (student server 2.2.0 address, teacher server release 2.1.2).
6. Cost: the Library tile "Adding books this month" should stay at cents.

---

## 7. Known gaps and risks
- Only the NCERT **prelims** are loaded; thresholds for "confident" are not calibrated on a real chapter (need 2 to 3 body chapters and about 15 questions).
- No scan reading (OCR); scanned pages are skipped and the book says so.
- About 0.4% of words from some PDFs come out split ("Pr ofessor").
- The model can still pick a wrong line; approval and the evals are the safety net.
- Existing projects do not get the `/knowledge/` line in `.gitignore` (student-owned file).
- Books can be copyrighted: the rules say so, but nothing enforces it.
- One reader limit: at most 2 books are read at once for the whole class.
- The AI provider can be slow or busy; agents report this cleanly but do not hide it.

---

## 8. Housekeeping to remember
- `test_claude` has: `knowledge/` (prelims copy and a note, plus a `.docx` that is skipped), an `evals/live.json`, temporary extras, and its own shelf `science` duplicating the library book. Remove the duplicate when testing ends.
- Memory file with the full decision log: `project-rag-agentic-direction.md` (in the Claude memory folder).
- Documents: `docs/KNOWLEDGE_CONNECTOR.md`, `starter/template/PATTERNS.md`, `CHANGELOG.md` (entries under 2.2.0 unreleased), spec section 4.17.
