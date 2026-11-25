# Quality priorities before the client end-to-end test (2026-11-27)

Focus: **answer quality** (security and operations later). Question: is what is built ready for the client's end-to-end test, and what must change first? Evidence: the sandbox runs (`reports/2026-11-24-orchestrator`, `2026-11-25-mds-poc`), the code, and the plans of the last weeks.

## 1. Verdict
**Ready to test, but four quality gaps will show up in the client's data on day one. Fix those (about two days), then run the end-to-end test.** Everything else below can wait for what the test shows.

## 2. Critical: fix before the test (they change what the client sees)

| # | Gap (found in the code, not guessed) | Why it matters for this client | Fix | Size |
|---|---|---|---|---|
| **Q1** | **Hindi in Devanagari is invisible to keyword search.** `knowledge_core.WORD = [a-z0-9]+`: a book or a question written in Devanagari gets no keyword match at all (only the vector search works), and the "confidence" gate leans on the same words. | The client and customers write Hindi, Hinglish and English. A book in Devanagari, or a question like "लाइब्रेरी की फीस?", is half-blind. | Let the tokenizer keep Devanagari (and test Hindi, Hinglish, English mixed); **rebuild the index of existing shelves** (a "rebuild" step or re-add the books). Sandbox check with the client's real Hindi lines. | small |
| **Q2** | **The agent does not know today's date or time.** Nothing in the prompt gives the date, the weekday or whether the office is open now. | The Classes Schedule desk gets "aaj class hai?", "is hafte", "kal"; the library gets "abhi khula hai?". It will guess or refuse. | Put "Now: Friday 27 Nov 2026, 3:40 PM (India time)" in every prompt (deterministic, no model call); teach the schedule line format to read days. Add office-hours awareness to the hand-over sentence ("our team will reply at 10 AM"). | small |
| **Q3** | **No fast way to measure a book.** The only check is typing questions one by one in "Ask the books"; the client has 45 gold questions and will change books many times. | Data iteration speed decides whether the test finishes in a week. | A **Books check** table in the Knowledge tab: questions with the phrase that must appear in the first passage; Run: "41 of 45 found, these 4 did not" (embedding cost only, cents). Same idea as Your tests, but for the books alone. | medium |
| **Q4** | **A book that was read badly is not flagged.** Tables become running text, columns interleave, scanned pages are skipped; the owner only sees "added". | The schedule and the fee lists are likely tables. A wrong timetable is the worst failure. | After reading, show **warnings per book** (pages skipped as scans, pages with very little text, many short numeric lines = probably a table) and point to "Read". Rule for the client meanwhile: fee and schedule tables as text/Markdown lines (already in the plan). | medium |

## 3. Medium: improves the test, can follow the first round

| # | Item | Why | Size |
|---|---|---|---|
| Q5 | **Small talk and closings** ("thanks", "ok", "bye", "haan"): today they fall to the menu or the fallback. A short deterministic closing reply per language. | Real chats are full of these; showing the menu after "dhanyavaad" feels broken. | small |
| Q6 | **Close the loop from Activity:** on a fallback or a hand-over, one click to "add a correction" or "note for the book" or "make a test row" (the last exists). | The daily care routine is reading failures and fixing them; make it two clicks. | medium |
| Q7 | **A message about two desks:** answer the first and offer the second, instead of only the menu. | "library aur class ki fees?" is common for this client. | medium |
| Q8 | **Shelf shows which agents use it**, and a **staging copy** of a shelf (test a changed book without changing the live answers). | Books are not versioned; editing a live shelf changes customers' answers at once. | medium |
| Q9 | **Reply-time measure in Activity** (webhook to reply). | Gemini varies from 1 to 15 s; measure before promising. | small |
| Q10 | **UI:** show the corrections and the schedule file's "updated on" date on the Knowledge tab; a "last changed" per book; Overview: fallback percent for the live version. | The owner sees stale data at a glance. | small |

## 4. Low: after the test
- **Tables and scans read well:** the model-reads-the-document reader (`KNOWLEDGE-READER-PLAN.md`, `KNOWLEDGE-DOCUMENT-MODE-PLAN.md`). Large; only if the client's real PDFs prove that text/Markdown is not enough.
- A retrieval step that rewrites short follow-ups; bigger answer cache; a stronger model for failed turns; interactive WhatsApp lists; per-agent shelves; roles; leads; export formats.
- Everything already listed in `READINESS-REVIEW-2026-11-26.md` (P2/P3).

## 5. What the old plans say now
| Plan | State |
|---|---|
| Test suites, repeatability, consistency, routing, books, archive, rename, alarms | built and deployed |
| Orchestrator O1 to O4 | built; O5 (stronger model, lists, multi-topic) waits for evidence (Q7 is the cheap version) |
| Knowledge reader / document mode / quality (Docling) | plans only; **Q1, Q3, Q4 are the cheap parts of them** |
| Gemini key failover, Bedrock | Gemini works; Bedrock blocked by the account; not a quality blocker |
| Migration audit, own stack | later |

## 6. Proposed order
1. **Q1 + Q2** (one day, backend only, deployed with a change set).
2. **Q3 + Q4** (one to two days, platform + Knowledge tab).
3. **Run the client end-to-end test** (`CLIENT-E2E-ACTION-PLAN.md`) with those in place.
4. After the first test week: Q5 to Q10 in the order the failures show.

## 7. Decisions needed
- Agree that Q1 to Q4 come before the client test (recommended), or test first and fix after.
- For Q4: warnings only (recommended) or also auto-reject a book that looks badly read.
- For Q2: India time (IST) fixed, or the business's own time zone setting later (IST now).
