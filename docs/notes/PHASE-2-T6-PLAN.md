# T6 plan: Studio agent screens (no book upload yet)

Status: PLAN ONLY. No code until the user approves. T5 (hand-over to a person, stuck-item sweeper) is postponed until after Phase 1 is implemented (user decision).

## Decisions already made (from the user)
| Decision | Answer |
|---|---|
| Book upload from the web | Later. Teacher side will get a presigned-upload admin route (Option A) in a later step. Not in T6. |
| T6 scope | Agent screens only |
| Service key location | Studio backend env variable (`CHATWOOT_SERVICE_KEY`), ONE business for now |

## What T6 builds (Studio repo only; teacher repo untouched)
Studio is a control plane: the browser never sees the key. The browser calls Studio's own `/api/...`; Studio's backend calls the teacher's admin API with `Authorization: Bearer cs_<tenant>.<secret>`.

### Backend (`backend/`, Python stdlib, new file `backend/admin_client.py` + routes in `app.py`)
- Env: `CHATWOOT_ADMIN_URL` (base of the Chatwoot API, no trailing slash), `CHATWOOT_SERVICE_KEY`, `OWNER_EMAILS` (comma list of people allowed in).
- Every `/api/agents...` route first calls `class_auth.require_user`, then checks the user's email is in `OWNER_EMAILS`. Anyone else: 403.
- Thin proxy routes, one per admin route (the admin API already enforces tenant isolation and validation; Studio forwards its error `message` unchanged):
  - `GET /api/agents` -> `GET /admin/agents`
  - `PUT /api/agents/<name>` -> `PUT /admin/agents/<name>` (create or update; passes `expected_rev`)
  - `GET /api/agents/<name>/versions/<n>` -> same on the admin API
  - `PUT /api/agents/<name>/versions/<n>` -> publish a version (immutable)
  - `POST /api/agents/<name>/live` -> set live version (`{"version": n}`, 0 = none)
- Client rules: 10 s timeout, no redirects, only the one configured https host, never log the key or request bodies, map 401 to a plain "Studio is not connected to the platform" message (not "sign in again").
- Local mode (`npm run dev`): if the env vars are missing, a small in-memory fake admin so the screens can be tried offline, clearly labelled "practice mode".

### Frontend (`frontend/src/`, English UI)
1. **Agents list**: name, enabled, live version, versions count, Chatwoot account/inbox mapping. Button "New agent".
2. **Agent page**: header settings (Chatwoot account id, inbox ids, enabled) and a **version editor** with the fields the runtime already accepts: instructions, language (en / hi / hinglish), shelves (1 to 5 names), fallback text, max answer chars, mode (rag / auto). Field limits and help text come from the same limits the teacher validator uses; the server's `bad_request` message is shown as-is.
3. **Publish**: "Publish as version n+1" (PUT version, with n from the agent's `versions`), then a separate, explicit "Make live" (POST live). Two steps on purpose: publishing never changes what customers see.
4. **Versions**: read-only history; a past version can be loaded into the editor as the base of the next one; "Make live" on any version = rollback.
5. **Conflict handling**: `changed` (409) -> "Someone changed this agent. Reload." with a reload button; never auto-overwrite.
6. Not shown: books, usage, spend cap, tenant keys (still the teacher's local tool). A read-only note says "Books are added by your teacher."

## Commits (each reviewable alone, checkpoint after each)
- **C3** sign-in gate + `OWNER_EMAILS` + admin client + `/api/agents` GET with fake admin; backend tests.
- **C4** create/update agent, version editor, publish, make live (all backend routes + screens); backend tests with a fake admin that mimics 409 `changed`, `version_exists`, `out_of_order`.
- **C5** versions history, rollback, polish, README, REVIEW-GUIDE entry.
Each: `npm run check` and `npm run build` green. No deploy, no `push_project confirm=true` without explicit yes.

## Risks / unverified (will be said plainly in the report)
- Not run against the real admin API (not deployed). Fake admin follows the code in `lambda/chatwoot_admin.py`, not a live response.
- `CHATWOOT_ADMIN_URL` is a deployment value (from the stack output of the teacher's `ChatwootApi`); the user must set it and the service key in the portal's Environment variables.
- Single business only: a second business needs per-user keys (a later decision).
- The agent name space is shared by all businesses (admin returns 409 `name_taken`); the UI shows it as "choose another name".
- The teacher admin function `ANY /admin/{proxy+}` must be deployed (`ChatwootEnabled=true`) for anything to work.

## Needs from the user before C3
1. The email(s) for `OWNER_EMAILS`.
2. Approval of this plan (or changes).
