# Plan: enable traces on the live platform (plan id cfd65a383e33bf88)

Command that made it: node setup.mjs plan --profile default --enable traces
Result: 1 resource added, 19 modified, 0 removed, no data replaced. NOTHING is applied yet.

## Added (1)
- TracesTable (DynamoDB). Pay per use, costs nothing while idle. Every item expires by itself (30/60/90 days, default 90).
  It is kept (not deleted) even if the stack is ever removed.

## Modified (19), in plain words

Code or settings updates only (no new permissions). Each gets the new code files and two new settings, TRACES_ENABLED and TRACES_TABLE:
- AiProxyFunction, KnowledgeFunction, KnowledgeWorkerFunction, StorageFunction, DeployFunction (portal), GatewayFunction,
  JobRunnerFunction, SweeperFunction
- SweeperFunctionDaily (schedule rule) and SweeperFunctionDailyPermission: listed because the sweeper function changes; same schedule.
- The five APIs (AiProxyApi, BackendApi, DeployApi, KnowledgeApi, StorageApi): listed as modified. I did not change their
  definitions; I expect only a refresh. I have not confirmed exactly why they are listed.

New permissions (the only security-relevant part), three roles:
- AiProxyFunctionRole: read/write items in TracesTable, only keys starting "t#".
- KnowledgeFunctionRole: same, plus may count a student's daily traces (keys "trd#") in the tokens table.
- DeployFunctionRole (the portal): read, list and delete in TracesTable and its two indexes. It cannot write steps.
- GatewayFunctionRole: listed, but I added no permission to it.

## What changes for students after apply
Nothing visible. Traces record for students who use class AI (Gemini) or knowledge search, metadata only (no text stored),
until the teacher changes a student's setting. Answers are never blocked or slowed by tracing.

## Next steps after your approval
1. apply (plan id cfd65a383e33bf88)
2. you approve a publish (portal code)
3. I test on test_claude (real AWS): ask a question, read its trace, check expiry and delete.

Rollback: node setup.mjs plan/apply without --enable traces removes nothing stateful (the table is retained); the code is safe to leave.
