# Amazon Bedrock as a third model provider (plan v2, not built)

## Direction (from the teacher)
Not only a fallback. **Bedrock becomes a choice next to OpenRouter and Gemini**, selectable in the teacher portal, so answer quality can be compared directly by switching the provider and running the test suite. A fallback stays for when a provider fails.

## Why it is simple here
The whole platform runs in one AWS account. A Lambda calls Bedrock with its IAM role: **no key to paste for anyone**. A new AWS account only needs Bedrock model access switched on for the models used.

## Checked in this account (us-east-1)
`anthropic.claude-haiku-5-5` is ACTIVE; inference profiles `us.anthropic.claude-haiku-5-5` and `global.anthropic.claude-haiku-5-5` exist (Sonnet 5.5 too). Not yet checked: a real call, the account's quota, the price per million tokens.

## What exists today (so the change stays small)
- Per student, the teacher portal already has a provider choice (`openrouter` or `gemini`, file `lambda/ai.py`, `PROVIDERS`), a model menu per provider, a spend cap and a counter. Gemini students go through our own OpenAI-compatible proxy (`lambda/ai_proxy.py`) with a private key `ga_<slug>.<secret>`.
- The Chatwoot worker calls `gemini.chat` directly (Google keys in turns, then OpenRouter, built and committed).

## Design
1. **`lambda/bedrock.py`** (new): `chat(body, timeout)` translates the OpenAI-shaped request to Bedrock `Converse` and the answer back (with `usage`). Throttling becomes 429, busy 503, access denied 403, so the existing "refused, try the next" logic works. `models()` and `cost_micro(model, usage)` like `gemini.py`, from a price table in settings (USD per million tokens, input and output) for each Bedrock model offered.
2. **Third provider `bedrock` for students.** `PROVIDERS` gets `bedrock`; it uses the same proxy as Gemini (no key leaves the platform), its own model menu (Haiku 5.5 default) and the same cap and counter. Selected like the others in the teacher's AI dialog. The proxy picks the provider by the student's choice. Needs no secret, so it is always "available" once the model access exists.
3. **Chatwoot adapter.** A platform setting `ai.chatwootProvider` (`gemini` default, `bedrock`, `openrouter`), editable in the portal's settings page, and `ai.chatwootBedrockModel`. The worker asks that provider first. Changing it needs no deploy. The answer records which provider and key name answered (`via`), and the cost uses that provider's prices.
4. **Fallback chain stays** (a provider that is refused or fails passes the call on): chosen provider, then the others that are available. For Gemini: its keys take turns (built), then Bedrock, then OpenRouter.
5. **Permissions (`template.yaml`).** `bedrock:InvokeModel` on the offered models and inference profiles, only for the functions that call a model: the AI proxy and the Chatwoot worker. No student role. The model ids come from settings. Guard tests updated on purpose.
6. **Quality check built in.** The suite runs on the provider chosen in the portal: switch, run `suite-run`, compare passes, flaky cases, Hindi answers, cost and speed (the run records `via` and the cost). The standard suite becomes the way to compare providers.
7. **Tests.** Fake Bedrock client (translation, usage, errors to statuses), provider choice for students and for Chatwoot, fallback order, cost per provider, IAM guard.
8. **Docs.** Changelog, settings, API, the portal text for the provider menu, and a migration note: a new AWS account needs Bedrock access for the models and a quota check.

## Things to know
- **Cost.** Haiku is normally several times dearer than Gemini Flash-Lite (0.30 / 2.50 dollars per million tokens). Check the Bedrock price page and put the real prices in the settings; they decide the caps.
- **Quota.** New models often start with a low requests-per-minute limit; the suite would show it as 429. Service Quotas can raise it.
- **Region.** `us.` profile keeps traffic in US regions (recommended); `global.` may route anywhere.
- Embeddings (book search) are not part of this change.

## Questions
- Q1 For Chatwoot, one platform-wide setting (recommended: simple, changeable in the portal) or a choice per business?
- Q2 Which Bedrock models in the menu: only Haiku 5.5, or Haiku 5.5 and Sonnet 5.5 (recommended: both, Haiku the default)?
