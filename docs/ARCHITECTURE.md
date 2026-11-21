# Architecture (Chatwoot agent platform part)

This file explains what lives where and how the pieces fit, for a person taking over. The whole class platform is described in `docs/TEACHER_ARCHITECTURE.md`; the adapter in detail in `docs/CHATWOOT_ADAPTER.md`; every limit in `docs/SETTINGS.md`.

## The flow of a customer message

```
Chatwoot --webhook--> ChatwootApi --> ChatwootReceiverFunction (chatwoot_api.handler)
     verify signature, ignore bot/agent/private messages, one conditional write per message (dedupe), answer 200 at once
                                 |  async invoke
                                 v
                      ChatwootWorkerFunction (chatwoot_api.worker)
     claim the message -> hand-over rules -> search the books -> score the extras -> one model call -> post the reply
     -> post each chosen media item -> record the outcome (numbers and codes only, never the customer's words)
```

Studio and the teacher's command talk to `ChatwootAdminFunction` (`/admin/...`, one service key per business) and to `tools/chatwoot_ctl.py` (direct table access with the teacher's AWS credentials).

## Where things live

| Thing | Where |
|---|---|
| Businesses (tenants), agents, immutable versions, the live pointer | DynamoDB `ChatwootTable`: `tenant#`, `agent#`, `agent#<name>#v<n>` (`chatwoot_registry.py`, `chatwoot_store.py`) |
| A version's behaviour | its `config`: instructions, language, shelves, fallback, hand-over reasons, provider and model, **extras**, **media** (`chatwoot_runtime.clean_config`) |
| Books (the facts) | S3 `knowledge/tenant/<business>/<shelf>/` with an own index (`knowledge_store.py`, `knowledge_core.py`); searched by `chatwoot_runtime.search_shelves` |
| Extras (the client's corrections) | inside the version's config as dated records; scored by meaning at answer time (`chatwoot_runtime.relevant_extras`, `score_extras`) |
| Media files | S3 `chatwoot-media/<business>/<agent>/`; counted in the ledger `media#<business>`; cleaned by `ChatwootJanitorFunction` (`chatwoot_media.py`, `chatwoot_janitor.py`) |
| Conversation memory | its own table, a few turns, short life (`chatwoot_memory.py`) |
| Teach sessions (the client teaches the agent) | `teach#...` items (`chatwoot_teach.py`, `chatwoot_interview.py`) |
| Test suites and runs | `suite#`, `suitestate#`, `suiterun#` (`chatwoot_suite.py`, worker `kind: suite`) |
| Models | `gemini.py` (several Google keys in turns, OpenRouter last), `bedrock.py` (Claude through Converse); the worker asks the version's provider first and the other as the spare |
| Every number that can be tuned | `config/settings.json` (generated docs in `docs/SETTINGS.md`) |
| Infrastructure | `template.yaml` (SAM); the adapter is created only when `ChatwootEnabled` is true |

## The answer, step by step (`chatwoot_runtime.respond`)

1. A message that asks for a person, complains, or is a photo or file without words is handed to a person (`chatwoot_handover.py`), no model call.
2. The books are searched; `confident` means the best passage is close in meaning or contains most of the question's words.
3. The version's extras are scored against the question (one cached embedding call). Relevant extras (close enough, best few) are put ahead of the books, **newest first**, and the model is told they win over the books and the later one wins over an earlier one.
4. `confident` is true when the books are sure **or** an extra is relevant. Otherwise the answer is the fallback text (the second fallback in a row hands over).
5. One model call; the reply is cleaned and cut; a trailing `MEDIA: id` line chooses media from the version's list; the worker posts the text, then each media item.

## Boundaries that must stay

- Only the worker reads the bot token and the Google/Bedrock access; the receiver and the admin function cannot.
- The admin function may only write registry, suite, teach and media-ledger items and sign upload forms for `chatwoot-media/`; the janitor may only delete there.
- A customer's words are never stored or logged outside the short conversation memory.
