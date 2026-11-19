# Sign in for student apps: design and status

> **Status: built and released in 2.8.0, 2.9.0 and 2.10.0.** This document records the design as it was built, and what is still to do. The how-to for teachers and students is [SIGN_IN.md](SIGN_IN.md). Remaining items are in [FUTURE.md](FUTURE.md) section 7. The file name is kept because code and the add-on catalogue refer to it.

## 1. Scope

Two sign-ins exist. This document covers only the second.

| Sign-in | Who uses it | Changed here? |
|---|---|---|
| The platform's own sign-in (the teacher's password on the portal; the student's token) | Teachers and students managing their platform | No |
| Sign-in for the apps students build (Google, for the visitors of a student's site or app) | The visitors of each student app | Yes |

## 2. Design as built

- **One Amazon Cognito user pool for the whole platform (one class).** It has one Google registration, which the teacher makes once. The teacher stores the Google client in Systems Manager (`/student-deploy/auth-google`); the platform refuses plainly if it is missing.
- **One app client per student app,** each with that app's own return address. Turning an app's sign-in off removes only that app's client.
- **PKCE, no client secret in the app.** The app's backend exchanges the one-time code, checks the signed token (signature, issuer, app client, expiry), and sets its **own** signed session cookie (default 7 days, `auth.sessionDays`). The page never sees a token.
- **Why one pool, not one pool per app:** every Cognito pool has its own sign-in domain, and Google needs each domain registered. One pool per app would mean one Google registration per student app. This was found before building, and it was an owner decision because it changes what is shared between apps.
- **Consequence:** one Google account is recognised across all student apps of a class. Visitor records are still stored per app. The visitor notice must say so (FUTURE.md A6).
- **Platform permissions:** the deploy function may create and manage pools and clients, and read the one parameter. Cognito has no resource-level limit on creating pools, so the grant is account-wide within the template.

## 3. Build order and status

| Step | What | Status |
|---|---|---|
| A0 | Owner's decision: Cognito with Google, one pool for the class, any Google account by default | Done |
| A1 | Platform side: the Google setting, one pool, one client per app, turn on and off, removal with the app | Released 2.8.0; permissions applied on the author's platform |
| A2 | The student's switch (`npm run signin`), the helper (Python and Node), the page parts, the example private route | Released 2.9.0; tested end to end on a real platform with a real Google account (sign in, sign out, a second account, the private route, turning it off) |
| A3 | The kit's rules, and `npm run check`, which warns about a login of the app's own (a password field, a token in browser storage, a `/login` or `/signup` route, password hashing or token making) and points to the platform's sign-in | Released 2.9.0 |
| Add-on | `sign-in` add-on for apps made before kit 2.9.0 | Released 2.10.0; tested live on a real app |
| A4 | Teacher view: monthly users per app, a sign-in switch per app, a per-app user cap | Not built: [FUTURE.md](FUTURE.md) |
| A5 | Google's consent-screen rules checked before a pilot | Not done: [FUTURE.md](FUTURE.md) |
| A6 | Visitor privacy notice about the shared pool | Not done: [FUTURE.md](FUTURE.md) |
| A7 | Email sign-in or other providers, on request only | Not planned: [FUTURE.md](FUTURE.md) |

## 4. Rules that keep it safe

- Only Cognito's signed tokens are trusted; a failed check signs nobody in.
- Each app client lists only its own app's return address.
- Cookies are set on the app's own address: secure, not readable by page scripts, same site only.
- Sessions expire after the length in the settings registry.
- Logs never contain a token or a one-time code.
- The app holds only what it needs to check tokens, provided through its environment.
- Deleting an app removes its app client; its visitors' records in the app's own storage go with the app.

## 5. The standard the AI editor follows

The kit's rules tell the editor: never write a login form, a password field or a token of your own; the backend decides who the user is (never trust a user id sent by the page); protect routes on the backend and check ownership; the page asks the backend "who am I"; store the minimum (a stable id, the email, the name); sign-out ends the session on the backend.

## 6. Cost

The design was costed against Cognito's free tier, which covers 10,000 monthly active users per account, well above a class (checked 2026-11-12). Confirm current pricing on the AWS pricing page before a large class relies on it. The installer's budget alert ([TEACHER_SETUP.md](TEACHER_SETUP.md)) covers surprises.
