# Gemini key failover (plan, not built)

## Why
The first real suite runs showed Gemini answering HTTP 429 (too many requests) for 3 to 5 of 31 cases, a different set each run. The same would hit real customers when answers are asked together. A pause and one retry (built, `chatwoot.llmRetryPauseSeconds`) helps; a second key helps more.

## What exists
The secret `openrouter` (Secrets Manager) already holds `GOOGLE_API_KEY`, `GOOGLE_API_KEY_ANIVESH`, `GOOGLE_API_KEY_SASHI`. Today `gemini.google_key()` reads only `GOOGLE_API_KEY`. No template change is needed: the same secret is already readable.

## Design
1. **Key list.** `gemini.google_keys()` returns `GOOGLE_API_KEY` first, then every name starting `GOOGLE_API_KEY_` in alphabetical order (names only are ever logged, never values). Cached like today.
2. **Failover.** `gemini.chat` tries the keys in order. On 429, 503 or a quota 403 it goes straight to the next key (no pause). A key that was refused is skipped for `gemini.keyCooldownSeconds` (default 60) in that container, so a busy key is not asked again at once.
3. **Pause only when every key refused.** Then the existing single retry after `llmRetryPauseSeconds` applies, and only after that the customer is handed over.
4. **Callers.** The change sits in `gemini.chat`, so the Chatwoot worker, the suite and the class AI proxy all gain it. Embeddings (`embeddings._google_key`) stay on the primary key for now.
5. **Diagnosis.** Each answer's metrics and each suite case record carry the key's *name* (for example `GOOGLE_API_KEY_SASHI`) and how many keys were tried, so a spend or quota problem can be traced. A cost line per key is a later step.
6. **Tests.** `gemini.transport` stub per key: first key 429 then second 200; all keys 429 then error; a 400 does not move to the next key; cooldown skips the refused key; names with other prefixes are ignored; a key value never appears in an error or log.
7. **Settings / docs.** One setting (`gemini.keyCooldownSeconds`), docs and changelog; a deploy change set of Modify rows only.

## Things to know before building
- **A 429 is counted per Google Cloud project, not per key.** Keys from the same project share one quota and failover gains nothing. It helps only if the keys come from different projects.
- **Whose money.** Each key bills the account that owns it. `GOOGLE_API_KEY_SASHI` is presumably another person's: they should know their key now serves the class, and the spend is theirs. The 10 US dollar monthly cap per business counts our side only.
- **Google's terms.** Making many projects only to multiply a free quota may break their terms. A paid tier on one project (higher limits) is the safer long-term fix; failover is a good safety net either way.
- Names must start with `GOOGLE_API_KEY_` (your secret uses `GOOGLE_`, not `gemini_`).

## Decisions
- A1 order: primary first, spill over on refusal (recommended: predictable spend) or round-robin (spreads load, spend splits across accounts).
- A2 scope: all callers of `gemini.chat` (recommended) or only the Chatwoot worker.
- A3 keep the pause-and-retry after all keys fail (recommended).
