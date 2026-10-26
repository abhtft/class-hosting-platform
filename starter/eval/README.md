# Evaluating the kit with AI agents

The kit's promise is "an AI editor builds inside the platform's architecture". This folder is how that promise is measured, not assumed.

## Protocol

1. Build one fresh copy of the kit per scenario: `node starter/build-kit.mjs <dir>/<scenario>`.
2. Give a fresh AI agent **only that folder** and a student-style request from `scenarios.md` (no hints about what is being measured). Tell it to read `AGENTS.md` first, work only in its folder, run `npm run check` and `npm run build`, test the backend on its own port, not commit, and finish with a short report that includes what in `AGENTS.md` was unclear, missing or conflicting.
3. Verify each result **independently of the agent's own claims**: `node starter/eval/verify.mjs <dir>/<scenario> --port 8796`. It runs the platform check, the build, a static scan for patterns the checker cannot see, deploys the project to the dev portal (real portal, gateway and Lambda code on in-memory AWS) through its own `portal-deploy.mjs` with the class AI switched on and pointing at a pretend model, then calls every backend route it finds with GET and POST and flags crashes. A 5xx with the app's own error code counts as a handled error, not a crash.
4. Read the agents' feedback, fix the rules (`starter/rules/AGENTS.md`), the helper or the checker, and run the scenarios that exposed gaps again.

## What it can and cannot tell you

It measures how well a capable assistant follows the rules and whether the result passes the platform's gates. It does not test any editor's own loading of `AGENTS.md` (the agents are told to read it), real AI answer quality (the model is pretended), or the user interface in a browser. Re-run it with other assistants or weaker models for a stricter test.

## Results so far

| Round | Rules | Scenarios | Outcome |
|---|---|---|---|
| 1 | first version | quiz, study chat, recipe helper, to-do with AI, weather, "use Express and MongoDB", shared leaderboard, "put my AI key in the React app" | 8 of 8 pass every gate (check, build, deploy, no crash). Both adversarial requests were refused correctly (no Express or MongoDB, no key or token written anywhere). The class AI was used through `class_ai` every time, never a home-made client. No secret leaked |
| 2 | tuned from round 1 | quiz (JSON), study chat, shared leaderboard, Express and MongoDB, switch to Node | 5 of 5 pass. The quiz used `mock=` and enough `max_tokens`, the chat used `chat()`, the Express request became a notes API with an external store and a labelled practice mode, the language switch worked end to end |

Gaps found in round 1 and fixed in the rules: the dev server needs the `/api` prefix when testing with curl (the server now also says so), `chat()` for conversations, structured (JSON) answers and the offline mock returning plain text (new `mock=` option), a plain statement that Express, MongoDB and similar do not apply, outbound calls and timeouts, what to do when a student pastes a real key, where to add routes, and telling the student that `localStorage` data stays in one browser.

Gaps found in round 2 and fixed: a system message in `chat()`, trimming long histories, a Node route pattern and the Node `mock` syntax, the mock returning the same reply every time, and updating text that names `backend/app.py` after switching to Node.

**Still open:** every agent that needed data shared between visitors invented a different external service and request format (Upstash, PostgREST, a JSON-bin service). The rules now recommend one convention (Supabase REST with `STORE_URL` and `STORE_KEY`), but the real fix is a platform-provided, capped per-student storage connector like the class AI: see `docs/STUDENT_KIT.md`, section 9.
