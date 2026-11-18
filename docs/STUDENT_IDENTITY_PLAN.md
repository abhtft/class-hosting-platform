# Plan: student identity (Google sign-in), personal tokens, and access requests

> **Status (2.11.0, not released): phases 1 to 4 are built and tested with stand-ins; the real-Cognito round trip is not yet tested live.** The guide for teachers and students is [STUDENT_IDENTITY.md](STUDENT_IDENTITY.md). The decisions in section 10 were all taken as recommended.

> Planning only. No code. Private (`.temp/` is git-ignored). Written 2026-11-18 against version 2.10.0.
> Source: the owner's draft ("token + email as security, verified by OAuth; a different token per user; student home with projects; request access; teacher accepts, rejects or edits").

---

## 1. Verdict: does the draft make a meaningful change?

**Yes, but for a different reason than the draft gives.**

| Claim in the draft | My assessment |
|---|---|
| "Token + email verified by OAuth is more secure" | **Partly.** On the web portal, yes: a stolen token alone no longer opens the portal. On the command line (deploy, push, pull) the token is still a bearer secret, so the email check only happens when the token is issued or renewed. The security gain is real but smaller than it sounds. It becomes strong only if personal tokens are **short-lived and renewed through Google sign-in**. |
| "A token per user, valid only for that OAuth identity" | **Good.** Today a token is a bearer secret that belongs to a *site* (`tok#<sha256>` + slug lock). Whoever holds it is the student. Binding it to a Google `sub` gives accountability, recovery without the teacher, and no token handed around between friends. |
| "Student sees their projects, the teacher's projects, and can request access" | **This is the real value.** Today the teacher must create a token, copy it, and send it to each student by hand. A request queue removes that step and is what makes a 30-student class (and open source) workable. |
| "Request a completely new project with my expected settings; teacher accepts, rejects or edits" | **High value, low risk** if the grant is clamped by the platform limits. It generalises the existing "regen request / approve / deny" flow. |

What the draft does **not** yet answer (these are the risks):

1. **Who may sign in?** Our Cognito pool accepts *any* Google account (AUTH_PLAN A0). If the same rule applies to the portal, any stranger can create a request on a teacher's platform. A teacher-controlled admission rule is mandatory (section 5).
2. **The pool is shared with app visitors.** A visitor of one student's app has a Google account in the same pool. The portal must use its **own app client** and check the token's audience, or a visitor's token could be replayed against the portal.
3. **Personal data.** Storing email and name makes `docs/PRIVACY.md` and the "Erase this student" action (action plan P0-4, P0-5) **prerequisites**, not extras.
4. **Timing.** The roadmap said "freeze features, fix P0, pilot with 3 to 5 teachers". This feature is the right next feature, but it should start **after** the clean-room install and the privacy/erase work (section 9).

**Recommendation:** build it, in four phases, with Google sign-in and requests first and personal tokens last.

---

## 2. What exists today (verified in code)

- A student token `mds_<token_urlsafe(16)>` is created by the teacher (`create_token`, lambda/portal.py:440). Only its SHA-256 is stored: `tok#<sha256>`. A slug lock `slug#<slug>` points to it. **One token = one student = one site.**
- `POST /api/student/login {token}` returns a signed **12-hour student session** whose subject is the token id (`student_login`, `student_token`). The kit's `npm run login` and the portal both use this. So the long-lived secret is only used at login; everything after uses the session. **This is what makes personal tokens cheap to add.**
- Teacher session and student session use different signing keys (`signing_key(role)`).
- An access-style request already exists for regeneration: `regen` on the token item, `approve {days}` / `deny {reason}`.
- Projects (project sync): one personal project per token, team projects made by the teacher with a member list (docs/PROJECT_SYNC.md).
- Cognito: one pool per class, one app client per student app, PKCE, Google as the only provider (lambda/auth.py, AUTH_PLAN). `platform_pool()` creates it. Free tier covers 10,000 monthly users.
- The student MCP never receives the token (it runs the project's own login script).

---

## 3. Target design

### 3.1 Two kinds of token

| | Universal token (existing) | Personal token (new) |
|---|---|---|
| Prefix | `mds_` (unchanged) | `mdp_` (suggested; see note) |
| Made by | Teacher, in the teacher page | The platform, for a signed-in student, after approval |
| Bound to | Nothing; whoever holds it | One Google identity (`sub`) |
| Life | Teacher's choice (`tokens.maxDays`) | Short by default (suggest 30 days), renewed by signing in again |
| Used for | Teacher-run classes, demos, people without Google, fallback | Everything a signed-in student does |
| Stays? | **Yes, unchanged** | New |

Notes on the prefix:
- The draft suggested "mdes..." for universal and "temp..." for personal. "temp" would be misleading if the token lasts 30 days, so I suggest **`mdp_`** (personal). Keep `mds_` for the existing kind. The prefix is a hint for humans and secret scanners (GitHub secret-scanning custom patterns, log filters). **The server must never trust the prefix.** The record has a `kind` field (`universal` | `personal`) and that is what decides.
- "Universal" in the draft could also mean a **class join code** (one code, many students). I do not recommend it: it is a shared secret again. Open question Q1.

### 3.2 New items in the existing tokens table (no new table)

| Key | Purpose |
|---|---|
| `usr#<sub>` | A student identity: `sub`, `email`, `email_verified`, `name`, `status` (active/disabled), `first_seen`, `last_seen`, list of token ids. Key on `sub`, never on email (emails change, and Workspace addresses are reused). |
| `tok#<sha256>` | Gets `kind`, `owner_sub` (null for universal), `device` label (optional). |
| `req#<id>` | An access request (section 3.5). TTL so old ones disappear. |
| `adm#rules` | Admission rules: mode, allowed emails, allowed domains, limits. |
| `pair#<code>` | Short-lived device pairing record (phase 4), with TTL. |

All are plain items in the one table the platform already backs up (PITR) and exports. No template change for storage.

### 3.3 Student flows

**Web (phases 1-3)**
1. Student opens the portal, chooses **Sign in with Google** (or pastes a universal token, as today).
2. Portal uses a new Cognito **app client "portal"** (PKCE, callback = portal URL). The portal Lambda verifies the ID token: signature (JWKS), issuer, **audience = the portal client id**, expiry, `email_verified = true`.
3. Admission check (section 5). If allowed, find or create `usr#<sub>`.
4. Issue a student session. Today the session subject is a token id; it must now be able to carry a `sub` as well. Design: session payload `{r:"student", s:<token id or null>, u:<sub or null>}`; a helper resolves "the caller" to a user plus the tokens they own.
5. **Student home** shows two lists (3.4).

**Command line / AI editor (phase 4)**
- `npm run login` starts a **device pairing**: the kit asks the platform for a pairing code, prints a short code and the portal URL; the student opens the portal (already signed in with Google) and approves "This computer"; the kit polls and receives a personal token (or directly a session). The token goes to `.env.local` as today. The AI editor still never sees it.
- Renewal: before expiry, `npm run login` repeats the pairing. A leaked personal token therefore stops working in at most its lifetime.
- Pasting a universal token keeps working exactly as today.

### 3.4 Student home

- **My projects:** the sites (tokens) the student owns, plus the personal project and the team projects they belong to. Each row shows status, expiry, features on.
- **Class projects:** every project the teacher has published to the class. **Green** = the student has access; **red** = not granted. Red rows have **Request access**.
- **Request a new project:** a form with the settings the student wants (section 3.5).
- **My requests:** pending / approved / rejected / edited, with the teacher's note.

What is "a project of the teacher"? Candidates: (a) team projects, (b) teacher-published assignments/templates, (c) the teacher's own sites. Today only (a) exists. Open question Q2. Plan assumes (a) first, with a small "listed in the class catalogue" flag per team project.

### 3.5 Requests

One generic item, `req#<id>`:

```
kind:        join_project | new_site | (later) extend | regen
requester:   sub
state:       pending | approved | rejected | withdrawn | expired
requested:   { slug?, days, features: { backend, ai, storage, knowledge, traces, domain }, limits..., project_id?, note }
granted:     { ...final values after the teacher's edit }   (only when approved)
decided_by, decided_at, teacher_note
```

Rules:
- **Clamp** every requested number to the platform settings (`tokens.maxDays`, backend/ai/storage/knowledge maxima). A student cannot ask for more than the platform allows, whatever they send.
- **Teacher inbox** (new teacher-page tab): list, filter, and three actions: **Accept as asked**, **Reject (reason)**, **Accept with changes** (edit the settings, add a note). The student sees requested vs granted side by side.
- **Approval applies through the existing functions** (`create_token`, `set_backend`, `set_ai`, `set_storage`, `set_knowledge`, `set_traces`, domain access, team membership). No second implementation of limits.
- **Decide once:** approval uses a conditional write (`state = pending`), so two clicks cannot create two sites.
- Limits: a student may have at most N pending requests (suggest 3) and N requests per day; the platform has a global cap on pending requests.
- No e-mail in v1 (no SES). The inbox shows a badge. Notifications are a later option (Q8).

---

## 4. How "token + email" really works (be honest about the guarantee)

| Situation | What the platform can prove |
|---|---|
| Web portal, Google sign-in | The caller controls that Google account (OAuth). Strong. |
| Web portal, personal token pasted | Token is valid **and** it belongs to the signed-in Google identity. Two factors. |
| CLI deploy with a personal token | Token is valid, bound to a known identity, not expired, user not disabled. The email was proven **when the token was issued**. One factor at use time. |
| CLI with a universal token | As today. |

So "valid only for that OAuth" holds at **issue and renewal**, not on every CLI call. If you want every call tied to Google, the cost is a browser round trip every few hours; I do not recommend it. Short life + per-device revocation + "sign out everywhere" is the practical answer.

---

## 5. Admission: who may sign in (mandatory)

Teacher-controlled setting in the settings registry (`identity.*`), shown in the teacher page:

| Mode | Meaning | Default |
|---|---|---|
| `invite` | Only listed emails | |
| `domain` | Emails ending in listed domains (for a school Workspace) | **default when a domain list exists** |
| `request` | Any verified Google account may sign in and **ask**, but gets nothing until approved | allowed, never the default |
| `token-only` | Google sign-in off; platform behaves as today | **default for existing installs** |

Existing installs keep working with no change (`token-only`). The feature is opt-in.

Other rules:
- `email_verified` must be true. Use `sub` as the key.
- Rate-limit sign-in attempts and requests per IP and per identity (reuse the `fail#<ip>` pattern).
- A teacher can **disable** an identity: all its personal tokens stop working and its sessions end at the next check.

---

## 6. Phases

| Phase | Deliverable | Depends on | Size |
|---|---|---|---|
| **0** | Owner decisions (section 10), consent-screen check (A5), PRIVACY.md and erase-student built | action plan P0-4/5 | S |
| **1** | Identity foundation: portal Cognito client, JWT verification in the portal, `usr#`, admission rules, "link my Google to my existing token", settings `identity.*` | 0 | M |
| **2** | Student home: My projects, Class projects (green/red), read-only | 1 | M |
| **3** | Requests: `req#`, Request access, Request new project, teacher inbox with accept / reject / edit, clamping, audit | 2 | L |
| **4** | Personal tokens (`mdp_`), device pairing in the kit, renewal, revoke per device, "sign out everywhere"; MCP unchanged | 1, 3 | L |
| **5** | Hardening: threat-model review, live test with 2 Google accounts, docs, upgrade notes, release | all | M |

Release suggestion: phases 1-3 as **2.11.0** (additive, opt-in), phase 4 as **2.12.0**. Nothing is tagged until you say "release X".

### Touch points per phase (so the plan can be checked against the code)

- **lambda/portal.py**: session payload gets a user id; new routes `/api/student/google/*`, `/api/student/home`, `/api/student/requests*`, `/api/admin/requests*`, `/api/admin/identity*`; `view()` unchanged for existing fields.
- **lambda/auth.py**: add a *platform* app client for the portal (separate from per-app clients); reuse `platform_pool()`.
- **JWT verification**: decide the library (vendored PyJWT + cryptography wheel vs a small RS256 verifier with the standard library plus a JWKS cache). Lambda size and the arm64 wheel are the cost. Q6.
- **template.yaml**: probably no new resources; check the deploy role already has `cognito-idp:*Client` on the pool (it does for app clients) and that the portal function may read the pool's JWKS (public URL, no permission).
- **config/settings.json**: new `identity.*` and `requests.*` settings (mode, domains, personal token days, pending cap, daily cap); docs/SETTINGS.md regenerates.
- **web/student.html, student.js**: sign-in button, home, request forms. **web/teacher.js**: Identity settings and Requests inbox tabs.
- **starter/** (kit): `login` script gets the pairing flow; kit version bumps; the signed kit manifest changes; `tests/kit-versions.json` updated.
- **student MCP / teacher MCP**: student MCP unchanged (still no token). Teacher MCP: **read-only** `list_requests`; **no** approve tool at first (approval is a human action, same principle as "no destroy tool"). Q9.
- **docs**: AUTH_PLAN gets a new section or a sibling doc; PRIVACY.md; TEACHER_SETUP; STUDENT_KIT; API_REFERENCE.

---

## 7. Security review (what to build in, not bolt on)

| Risk | Control |
|---|---|
| Stranger signs in with any Gmail | Admission modes; default off; `request` mode never grants anything automatically |
| Visitor of a student app replays their token on the portal | Separate app client; check `aud` = portal client id; check `token_use = id` |
| Token confusion between session roles | Keep separate signing keys; add `u` claim only to student sessions |
| Email takeover (address reused) | Key on `sub`; store email for display only |
| Double approval / race | Conditional write on `state = pending` |
| Student asks for huge limits | Clamp on the server; teacher edit also clamped |
| Request spam | Per-identity and global caps, TTL on `req#` |
| IDOR (student A reads B's requests) | Every route derives the owner from the session, never from a body field; add an authorisation test matrix |
| Teacher XSS from a student's note or name | Escape in the teacher page; length limits; no HTML |
| CLI pairing code guessing | 8+ characters from a safe alphabet, 10-minute TTL, 5 tries, single use, bound to one approval |
| Leaked personal token | Short life, device label, revoke one device, sign out everywhere |
| PII | PRIVACY.md, export includes `usr#`, **erase-student removes `usr#`, `req#`, tokens, projects** |
| Google outage | Universal tokens still work; teacher can fall back to `token-only` |
| Cost | Cognito free tier (10,000 monthly users); requests and users are single small items in DynamoDB |

---

## 8. Tests to write first

1. **Unit (fakes)**: JWT verify with a test key pair (wrong aud, wrong iss, expired, `email_verified` false, wrong `token_use`); admission modes; clamping; state machine of a request; double approve; session carries `u` and the old session still works.
2. **Authorisation matrix**: student A vs B for every student route; student session on admin routes; admin session on student routes.
3. **Regression**: `mds_` login, deploy, push/pull, MCP flows unchanged; existing installs on `token-only` see no difference.
4. **Template tests**: if the template changes, existing `test_template_*` style checks.
5. **Concurrency**: two approvals at once; two requests at the cap.
6. **Live on test_claude** (real AWS, per the standing rule): two real Google accounts, one allowed and one not; request, edit, approve; pairing from the kit; revoke a device; disable an identity. Record it in the test log. **Never touch the production profile without instruction.**
7. **Kit/package**: the published npm tarball runs the new `login` (the lesson from the `.gitignore` bug).

---

## 9. Where this sits in the larger plan

- Do **first** (from the action plan): clean-room install on a second account, alerts and budget live, backup/restore proof, `docs/PRIVACY.md`, "Erase this student", the live sign-in test (A5).
- Then phases 1-3 (this plan). They also *help* the pilot: teachers stop copying tokens.
- Phase 4 after the pilot gives feedback; personal tokens are the most invasive part.
- Do not mix with unrelated changes in the same release.

---

## 10. Decisions needed from the owner (to finalise the steps)

| # | Question | My recommendation |
|---|---|---|
| Q1 | By "universal token" do you mean today's teacher-made `mds_` token, or a class-wide join code? | Today's `mds_` token. No shared join code. |
| Q2 | What is a "teacher's project" in the green/red list: team projects, teacher-published templates, or the teacher's sites? | Team projects first, with a "show in class catalogue" switch. |
| Q3 | Default admission mode for a new install? | `token-only` until the teacher turns Google on; then `domain` or `invite`. |
| Q4 | Personal token life? | 30 days, renewed by pairing. | 
| Q5 | Can one student own several sites? | Yes, through approved "new project" requests; one personal project stays. | 
| Q6 | JWT library: vendored PyJWT + cryptography, or a small hand-written RS256 verifier? | Decide after measuring Lambda package size; prefer the smaller. | 
| Q7 | Should an approval ever happen automatically (for example, join for allowed domain with default settings)? | Not in v1. Add as a teacher setting later. |
| Q8 | Notify the teacher by e-mail? | No in v1; badge in the portal. SES later. |
| Q9 | Should the teacher MCP list requests, and may it approve? | List only. Approve stays a human click. |
| Q10 | Release shape: 2.11.0 (phases 1-3) then 2.12.0 (phase 4)? | Yes. |

---

## 11. Out of scope for now

E-mail or password sign-in, other providers (Microsoft, GitHub), auto-approval rules, payment, per-student billing, notifications by e-mail, merging two identities, a public self-service sign-up that needs no teacher.

## 12. Definition of done (per phase)

- The behaviours in the phase work on `test_claude` with real Google accounts.
- All new routes have an authorisation test.
- Existing students on `mds_` tokens notice nothing.
- Docs and settings pages updated; PRIVACY.md mentions the new data.
- Nothing is committed, tagged or released without an explicit instruction.
