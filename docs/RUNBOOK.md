# Runbook (Chatwoot agent platform part)

How to run, check, deploy and debug. Commands are PowerShell or Git Bash on Windows; replace the names with the stack outputs.

## Run the tests (no AWS needed)

```powershell
node tests/run.mjs
```

One adapter test file alone: `python tests/test_chatwoot_runtime.py` (also `_api`, `_admin`, `_media`, `_teach`, `_suite`, `_handover`, `_core`, `_ctl`, `test_template_chatwoot.py`, `test_settings.py`).
After editing `config/settings.json` run `python tools/gen_settings.py` (it rewrites `lambda/platform_settings_data.py` and `docs/SETTINGS.md`).

## Before every deploy

1. Work from a clean committed tree: `git worktree add --detach <folder> HEAD` and package from there, never from a dirty working folder.
2. Checkov: `checkov -f template.yaml --config-file .checkov.yaml` (exceptions only in `.checkov.yaml`, each with a reason).
3. cfn-lint: `cfn-lint -i W1030 W2506 -t template.yaml`.
4. Create the change set without executing it, read it (anything Add, Remove or Replace is a decision), and execute only on the owner's yes. A change set keeps every live parameter as it is.

## After every deploy

Run the agent's test suite on the live version and read the failed cases:

```powershell
python tools/chatwoot_ctl.py --table <ChatwootTableName> --worker <ChatwootWorkerFunction> suite-run shop --tenant poc --wait
```

## Teacher commands (`tools/chatwoot_ctl.py`)

`create-tenant`, `rotate-key`, `disable-tenant`, `set-cap`, `set-limits <business> name=number` (media limits), `agent-set`, `publish`, `set-live`, `knowledge-sync`, `eval`, `suite-load`, `suite-show`, `suite-run`, `list`, `show-agent`.

## Debugging an answer

- **Test chat / dry run** (worker `kind: test`, no message is posted): shows the route, the book passages, the extras used (title "Update of <date>") and the media it would send.
- **Worker log lines** are JSON with names and codes only. A handover or failure reason is in the message item (`msg#<agent>#<id>`): `llm_error`, `retrieval_error`, `no_shelf`, `cap_reached`, `bad_config`, `stuck`.
- **The answer ignores a correction:** check the version's `extras` (date, text), the score in the dry run's explain list, and `chatwoot.extrasMinScore`.
- **The bot answers "hi" with a model answer or never hands over after two fallbacks:** an extra is being treated as relevant to everything; check its score and text in the explain list.
- **A media file did not arrive:** the message item's `media` list shows `attached`, `link`, `failed` or `unknown` (a timeout: not repeated). Check the file is in the ledger (`chatwoot_ctl.py set-limits <business>` shows use) and that its bytes match its type.
- **Bedrock refuses:** the account needs Bedrock model access for the Claude models; the other provider answers meanwhile.

## Recovering

- A bad version: `set-live` an earlier version number (versions never change). Retire a version only when it is not live and not the last one.
- A stuck message: the sweeper hands it to a person every 10 minutes.
