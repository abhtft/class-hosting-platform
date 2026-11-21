# Decisions (Chatwoot agent platform part)

Newest first. Each entry says what was decided, why, and what it costs us.

## 2026-11-21: Extras are retrieved by meaning and rank above the books
- **Decision.** The client's corrections (extras) are dated records. For each question they are scored against it; the relevant ones are shown to the model ahead of the books, newest first. A relevant extra makes the answer "confident" even when the books are not sure; with no relevant extra nothing changes.
- **Why.** Books are facts that change; the client's correction must win a conflict, and a later correction must win over an earlier one. The first implementation put every extra in every prompt and made every message confident: the live suite fell from 32/32 to 28/32 (welcome, off-topic, stuck hand-over broke).
- **Cost.** One extra embedding call for a version's extras the first time a container sees them (cached while warm); thresholds (`chatwoot.extrasMinScore`, `extrasTopK`) must be tuned on real embeddings.
- **Not done on purpose.** An explicit "replaces extra N" link (the later date decides); merging old extras into the books (the owner does it by hand).

## 2026-11-21: Media are described, never read
- **Decision.** A version lists photos, videos, files, links and places with a description; the model picks by description only; the worker sends them after the text. A customer's own photo or file without words goes to a person.
- **Why.** The owner wants the bot to forward prepared material, not to analyse it. Cheap, predictable, safe.
- **Cost/limits.** Per-business limits and totals (teacher-set), a ledger, a daily janitor, a version pinned to its files. The Chatwoot attachment upload with the bot token is not yet verified on the live Chatwoot.

## 2026-11-21: Build the agent core ourselves, buy only commodity parts
- **Decision.** Keep the core (businesses, versions, teach, suites, cost caps, hand-over) in our code on serverless AWS. Replace commodity parts (book search store, PDF parsing) only when real pain shows; the search sits behind one function so the change is small.
- **Why.** Agent frameworks do not give multi-business isolation, immutable versions, client teaching or regression suites; all-in-one platforms need servers and fight this design.
- **Next.** Track B of `docs/notes/EXTRAS-RETRIEVAL-PLAN.md`: book search on Bedrock Knowledge Bases with S3 Vectors (never OpenSearch Serverless: about $345 to $700 a month idle).

## Earlier
- Models: Gemini with several keys in turns and OpenRouter as the last way; Amazon Bedrock (Claude) as a selectable provider per version, the other provider as the spare.
- Deploy only from a clean committed tree through a change set that is shown and then executed on the owner's yes; Checkov and cfn-lint first.
