# Design: logs the student's AI agent can actually use

Status: proposal, nothing coded. Private (.temp is git-ignored).

## What exists today (checked in the code, not assumed)

- `GET /api/student/backend/logs` exists (`lambda/portal.py:525`, `lambda/backends.py:327`). Portal button "Show recent logs" (`web/backend.js:330`).
- Log groups for `sd-*` get a 7-day retention (`backends.py:37,242`). The Deploy Lambda already has `logs:FilterLogEvents` on `/aws/lambda/sd-*` (`template.yaml:794`). **No IAM or stack change is needed.**
- So "logs are not shown" is only partly true. The real gaps are below.

## Gaps

1. **The agent cannot read logs.** The editor tools (`starter/mcp/server.mjs`) are: init_project, check_project, deploy, status, update_kit. No `logs`. The kit's AGENTS.md tells the agent "read the logs in the portal", which an agent cannot do. A student has to copy-paste.
2. **The portal shows the wrong lines.** `filter_log_events(limit=50, startTime=now-1h)` returns the OLDEST 50 events of the hour. After 50 lines the student sees stale lines and misses the newest error.
3. **The useful part of an error is cut off.** Messages are cut to the first 500 characters; a Python traceback or Node stack puts the actual error at the END.
4. **Noise.** `START`, `END` lines drown the signal; `REPORT` lines (duration, memory, "Task timed out") are useful only sometimes.
5. **No filter.** Cannot ask for errors only, or a different time window.

## Proposal

### API (backends.py `logs()`)
`GET /api/student/backend/logs?minutes=60&limit=50&errors=1`
- `minutes` 1..180 (default 60), `limit` 1..100 (default 50), `errors=1` applies a CloudWatch filter pattern for `ERROR`, `Error`, `Exception`, `Traceback`, `Task timed out`.
- Page through results (max 5 pages) and keep the LAST `limit` events, so the newest win.
- Drop `START` / `END` lines; keep `REPORT` only when it contains "Task timed out" or a memory kill.
- Truncate long messages keeping the TAIL (last 1500 chars) plus a `[...cut]` marker.
- Mask the values of the student's own environment variables (6+ characters) as `***` before returning, so a pasted log or the agent's chat never carries a secret they printed by mistake.
- The log group still comes only from the student's own record (`function_name`), never from the request: a student cannot name another group.
- Response adds `{ lines, minutes, truncated, hint }`; old clients still work (`lines` unchanged).

### Kit and editor tools
- `platform.mjs`/`portal-deploy.mjs`: `node tools/platform.mjs logs [--errors] [--minutes 30]` (uses the student's token like deploy).
- MCP tool `logs` (args: errors, minutes) so the agent can call it itself.
- AGENTS.md: after a 502 `function_error` or a failed Test, call `logs --errors` first, fix, redeploy. One short paragraph, no new rules.
- `deploy` of a backend that was just published prints "if it fails: logs --errors".
- Kit change => bump `kitVersion` and record the hash in `tests/kit-versions.json`.

### Portal
- Add "Errors only" toggle and a window selector (15 min / 1 h / 3 h); show newest at the bottom, scroll to end.

## Out of scope (later)
- Teacher viewing any student's logs (separate permission decision).
- Metrics graphs; alarms per student; log search over days (retention is 7 days).
- Putting a request id in the 502 body to match a log line (small, worth doing next).

## Risks and checks
- CloudWatch cost: one page of FilterLogEvents per request, capped at 5 pages, and the endpoint gets the existing portal rate limit. Add a 10/minute per-student limit if the portal one is weaker.
- Masking can miss derived values (base64 of a key). Accept; say so in docs. Students are already told not to log secrets.
- Filter pattern syntax is easy to get wrong: verify once against real AWS (`teacher-test` profile), not only the fake.

## Tests
- Fake logs client with 120 events: the newest `limit` come back in order; START/END dropped; tail kept on long lines; env values masked; `errors=1` passes the pattern; unknown student function => empty; `minutes`/`limit` clamped.
- MCP tool test: `logs` is listed, calls the API with the token, prints lines.
- Kit hash guard and the existing suite.

## Delivery
Order: F1 hardening first, then this. One release (2.1.0: a new feature, kit changes). Needs `upgrade` for the Lambda code; students get the kit by auto-sync. Real-AWS check with the throwaway profile before tagging.

## Size
About a day: API + tests (half), kit tool + MCP + AGENTS.md (a quarter), portal toggle + docs + changelog (a quarter).
