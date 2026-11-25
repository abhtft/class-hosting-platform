# Readiness review: agent creation to knowledge (2026-11-26)

A review of everything built since the architecture review (2026-11-22): the five-tab Studio, Fill from the books, consistency check, orchestrator, Knowledge tab, archive, display name. Judged against: **robust, reliable, fast, cost-effective agents that a business owner can run without the developer**.

## 1. Verdict
**Ready to conclude as "v1 for a supervised pilot"** (Margdarshak and one or two friendly businesses, with the developer reachable). **Not yet ready to conclude as a product** many businesses use on their own. Between the two there are five **critical** items (section 3, P1); none is large. The rest is the next product phase, not a gap in what exists.

## 2. What is complete and sound
| Area | State |
|---|---|
| Create, configure, publish, make live, roll back | Complete: immutable versions, gate on the safety suite with a typed reason |
| Knowledge | Complete for text PDFs, text, Markdown, HTML: add, read, search, remove; one writer function; limits and cost counted |
| Quality loop | Complete: own tests (+ rows from the books), 52-case safety suite, repeatability, consistency across texts and departments, routing table |
| Corrections / teaching | Complete: embedded, newest wins, client link sessions |
| Orchestrator | Complete for rules, meaning, small model, menu, welcome, team hand-over; found and fixed real issues in a live run |
| Operations | Archive/restore with a daily purge; display name; traces and an Activity tab; per-business monthly cap; change sets with Checkov |
| Documentation and tests | One testing guide, runbook, 70-odd platform test steps, route tests in Studio |

## 3. Gaps, by priority

### P1: before more than a pilot (days, not weeks)
1. **Nobody is told when it breaks.** No alarm on worker errors, receiver 5xx, failed message items, hand-overs caused by model failures, or a spend jump. *Do:* four CloudWatch alarms on the existing alert topic.
2. **Open security items.** The Chatwoot bot secret seen in Railway logs is not rotated; Studio sign-in and the owner list must be confirmed on in production. *Do:* rotate, verify, write the result in the runbook.
3. **"Auto" mode is weaker than the main path** (no memory, notes, channel rule or media). *Do:* hide it in Studio until agent types need tools.
4. **The real WhatsApp round trip is untested** for the newest features: team assignment on hand-over, a department's media together with its text, "Open" silence, Knowledge upload with a real PDF. *Do:* the owner's HANDOFF steps; record the result.
5. **Backups and export.** A business cannot export an agent (config, tests, routing table) and the shelves live only in the bucket. *Do:* Export (a JSON of an agent) in Studio; confirm S3 versioning and DynamoDB point-in-time recovery are on for the tables (they are for the main table; check the books index and shelves).

### P2: the first weeks of real use
6. **No view of the conversations or the leads.** Activity shows answers, steps and cost without the customer's words (by design). A mobile number triggers a hand-over but is not saved as a lead. *Do:* a Leads record and card (name, number, need, conversation link) with CSV export; an optional "review these conversations" view with the privacy level the owner chooses.
7. **Spend is visible after the fact only.** The monthly cap exists; the owner is not warned at 80 percent, and testing and customers share one cap. *Do:* two buckets (customers, testing) and an 80 percent alert.
8. **One owner login.** Studio allows a list of owner e-mails with the same rights. *Do:* roles (owner, editor, viewer) and a change trail (who published, made live, removed a book).
9. **Books are shared and unversioned.** Removing a book changes live agents at once; a shelf belongs to the business, not an agent. *Do (small):* show "used by agents X, Y" on each shelf; later, a snapshot before removal.
10. **Scanned PDFs fail** ("no text"). *Do:* text recognition for scans as an opt-in step (cost shown first), or the "model reads the document" reader from `DOCUMENT-MODE-PLAN`.
11. **Latency tail.** Gemini calls take 1 to 15 seconds; a customer waits for a slow tail. *Do:* a shorter timeout with one retry on the other provider, and the end-to-end reply time in the trace (measure first).
12. **Studio speed.** Fewer requests were done; a new connection to the platform per call and cold starts remain. *Do:* connection reuse in Studio's backend; one "open agent" call.

### P3: later
13. **Own stack** for the agent product (student-hosting limits are shared): with the migration audit.
14. **More agent types** (lead capture, booking) with actions; **outbound follow-ups** with approved WhatsApp templates; **other channels** (web widget).
15. **Duplicate agent**, **bulk import** of tests, per-agent notes, tags.
16. **Docs consolidation:** one `AGENT-PLATFORM.md`; archive the superseded plans.

## 4. Proposed phases
| Phase | Work | Exit |
|---|---|---|
| **P (pilot hardening)** | Items 1 to 5; push and tag; the reports line in CLAUDE.md | Alarms e-mail within minutes; secret rotated; D5 and the Knowledge upload verified live; Export works |
| **Q (see and own)** | Items 6, 7, 8 (+ 9 hint) | Leads card and CSV; 80 percent alert; roles and a change trail |
| **R (depth)** | Items 10, 11, 12 | A scanned PDF can be read; the slowest 5 percent of replies are bounded |
| **S (growth)** | Items 13 to 16 | New agent types; own stack |

## 5. Decision
Conclude **phase P** (about a week) and call that the pilot release (`v2.14.0`). Start **Q** only after the owner has used the pilot on real WhatsApp for a few days: real conversations will show whether Leads or Roles matter more. Do not start S until then.
