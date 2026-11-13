# Sign-in for apps built on the platform (plan)

> **Status (2026-11-12): option A chosen by the owner. Step A1 (platform side) is built in code and tested, but not live.** Built: `lambda/auth.py` (one shared Cognito pool, the Google registration made once, one client per app with its own return address, sign-in turned off and on per app) and `tests/test_auth.py` (step 52 of the suite). **Permissions (planned, not applied):** the template now gives the deploy function the Cognito permissions and reads one more Systems Manager parameter, `/student-deploy/auth-google` (a new template parameter, registered in the settings registry). `node setup.mjs plan` shows this as plan id `3a6dd0310057ec5a`: it modifies 21 resources, adds none, removes none, and replaces no data. **Ordering (found 2026-11-13):** the Google registration needs the platform's sign-in address (its Cognito domain), and that domain is created the first time sign-in is turned on for an app. So the order is: (1) step A2 adds the switch for one test app, which creates the pool and shows the return address; (2) the teacher registers that address on the Google client and stores the client in the Systems Manager parameter; (3) the live test. **Done:** the permissions plan `3a6dd0310057ec5a` was applied on 2026-11-12, and release 2.8.0 contains the platform side (commit a2854c4). **Not yet done:** A2 (the switch and the sample page), the teacher's Google client, and the live test. Steps A2 to A5 are next. Request from the owner (2026-11-12): a standard way for the apps students build to sign people in, Gmail (Google) first, with a standard the AI editor follows so it never invents its own login. Budget from the owner: about **$2 to $5 a month** for sign-in and up to **$2 to $3 a month** for any other sign-in feature, paid from the AWS credit.

## 0. Scope: which sign-in this plan is about

Two different sign-ins exist. This plan covers only the second.

| Sign-in | Who uses it | Changed by this plan? |
|---|---|---|
| **The platform's own sign-in** (the teacher's password on the portal; the student's token) | Teachers and students managing their platform | **No** |
| **Sign-in for the apps students build** (Gmail or Google sign-in for the visitors of a student's website or app) | The visitors of each student app | **Yes: this is the plan** |

The Google client in section 5 belongs to the class, and it is used only for the visitors of student apps. The teacher's portal password and the student token stay as they are.

## 1. What exists today (checked in the code)

- **Teachers** sign in to the portal with a password.
- **Students** have a token per project. It proves the student to the platform, not to their app's visitors.
- **Visitors of a student's app** are unknown. The app's backend gets requests with no identity.
- A student's data (class storage, books) belongs to the **student**, not to the **visitor**. So an app cannot yet keep "this person's notes".

## 2. The recommendation: Amazon Cognito, Google sign-in, one user pool per app

Cognito is the AWS service for this, and it fits the budget. Its checked terms (AWS pricing page, 2026-11-12):

- **Free tier:** Lite and Essentials give **10,000 monthly active users per account, every month, with no expiry**. Social sign-ins such as Google count toward it.
- **Above the free tier:** Essentials is **$0.015 per monthly active user**. Lite is cheaper for basic sign-in (its price is on the pricing page; I did not confirm it).
- **Federated enterprise sign-in** (SAML or OIDC) has its own free allowance of 50 users; it is not needed here.
- **Identity pools** (temporary AWS access for visitors) are free, but not needed: the app only needs to know who the visitor is.

**Why Cognito beats building our own bridge:** Cognito already does the hard security work (the Google check, the codes, the token signing, the refresh and the sign-out). The platform would not be writing and maintaining a sign-in system. That is the lowest-risk option for a school, and the cost for a class is $0.

### The parts

| Part | Who owns it | What it does |
|---|---|---|
| **Google client** (once for the platform) | The teacher, once | A Google OAuth client for web sign-in, stored as a platform secret, like the AI keys |
| **User pool per student app** | The platform, created with the app's backend | A Cognito user pool with Google as its sign-in provider. One pool per app keeps each app's visitors separate from the others |
| **App client** | The platform, created with the pool | The app's own client. It lists the app's sign-in address as its return address (see the open point in section 8) |
| **Identity helper** | The kit (Python and Node) | Checks the pool's signed token on the backend, sets the app's own session cookie, and offers `current_user()` and `require_user()` |
| **Page parts** | The kit's frontend | A **Sign in with Google** button, sign-out, and a hook that gives the page the current user |

### The flow, in plain words
1. The visitor presses **Sign in with Google**.
2. The app goes to Cognito's sign-in page for its pool, which sends the visitor to Google.
3. Google confirms the person and sends them back to Cognito, which sends them to the app with a one-time code.
4. The app's backend exchanges the code for a signed token and checks it (issuer, app client, expiry).
5. The backend sets its own session cookie on the app's own address. The page never sees a token. Nothing sensitive is stored in the browser.

### Correction found before building (2026-11-12)
Each Cognito user pool has its own sign-in domain, and Google requires every such domain to be registered as a return address on the Google client. So **one pool per app would add a Google registration for every student app**, the same problem the bridge was meant to avoid. The corrected design:
- **One user pool for the platform (the class),** with one Google registration.
- **One app client per student app,** each with that app's own return address. A client holds one return address, so the per-client limit in section 8 no longer applies; the pool's limit on app clients is 1,000 by default.
- **Visitor records stay per app,** in each app's own storage. The shared pool means a Google account is recognised across student apps. The privacy line to visitors must say so.

This is an owner decision (section 9, question 1), because it changes isolation between apps.

## 3. The standard the AI editor follows

A short "sign-in contract" in the kit's rules, so every agent builds sign-in the same way:

1. **Never write a login form, a password field, a password check or a token of your own.** Use the sign-in helper and the sign-in button.
2. **The backend decides who the user is.** Use `current_user()`; never trust a user id sent by the page.
3. **Protect routes on the backend** with `require_user()`, and check ownership: a user reads and writes only their own records.
4. **The page never sees a token.** It asks the backend "who am I" and shows the answer.
5. **Store the minimum:** the stable id, the email and the name.
6. **Show a privacy line** to visitors: what is stored, and how to ask for deletion.
7. **Sign-out ends the session** on the backend.
8. **Session length and limits come from the settings registry**, so the teacher changes them in one place.

`npm run check` gets rules that catch the usual mistakes before a deploy: a password input without the helper, a token saved to local storage, a secret written into the frontend, and a route that changes data without `require_user()`.

## 4. Budget standard

| Feature | Monthly cost for a class | In budget? |
|---|---|---|
| Google sign-in with Cognito, up to 10,000 monthly users | **$0** | Yes |
| Above 10,000 monthly users at $0.015 each | About $0.015 per extra user | Yes: $2 to $3 covers about 130 to 200 extra users a month |
| Sign-in logs and the stored session | Negligible | Yes |
| Email sign-in or verification (email sent through SES, if ever added) | Small per email; not needed for Google | Only if added |
| SMS for MFA | Billed per message; not part of this plan | **No: not in the budget** |
| Extra sign-in providers (Apple, Microsoft and so on) | Free in Cognito, but each needs its own setup and review | Yes, as a teacher decision |

**Standard cost guard:**
- The portal shows the number of monthly users per app.
- The installer's budget alert (already in the platform) e-mails at 80% and 100% of the monthly budget the owner sets (for example $3).
- A teacher setting caps the sign-in users per app (the settings registry), with a clear message when the cap is reached. This keeps the cost predictable.

## 5. What the teacher does (once)

1. Create a Google Cloud project and an OAuth client for web sign-in. Register only Cognito's sign-in address as the redirect address (one address for the whole platform).
2. Store the Google client id and secret as platform secrets.
3. Decide who may sign in: **any Google account** (default), or **only listed addresses** (a class list). The list is enforced by the app's backend.
4. Google shows an "unverified app" screen to people who are not on its test list until the consent screen is published. Basic sign-in (name and email) needs only a simple publish step. **This must be checked against Google's current rules before the pilot.**

## 6. Rules that keep it safe

- **Only Cognito's signed tokens are trusted.** The backend checks the signature, the issuer, the app client, and the expiry. A failed check signs nobody in.
- **Return addresses are fixed.** Each app client lists only its own app's address. Nothing else can receive a code.
- **Cookies on the app's own address:** secure, not readable by page scripts, limited to the same site.
- **Sessions expire**, with the length set in the settings registry.
- **Logs never contain a token or a code.**
- **Rate limits** on sign-in starts, like the existing gateway limits.
- **Erasing a visitor:** the teacher can remove a visitor's records from an app and delete the visitor in the pool, the same way student data is erased today.
- **No secret in the student's code.** The app holds only what it needs to check tokens, provided through the environment, as the AI keys are.
- **Deleting an app** deletes its user pool, so its visitors' accounts go with it.

## 7. Build order

| Step | What | Done when |
|---|---|---|
| **A0. Decide** | Owner agrees Cognito with Google, one pool per app, the budget, and who may sign in (section 9) | Owner's yes |
| **A1. Platform setup** | The Google client stored as a secret; the platform creates a pool and an app client for each app at deploy; deleting an app deletes its pool | A test app gets a pool, and the pool is removed with the app |
| **A2. Helpers and parts** | The kit's identity helper (Python and Node), the sign-in button and user hook, a sample route | A sample app signs in, sees its user, signs out; a second visitor sees only their own records |
| **A3. Contract and checks** | The AGENTS.md section, the PATTERNS.md recipe, the `npm run check` rules, an eval case that refuses a hand-made login | The check flags a hand-made login; the eval passes |
| **A4. Teacher view and switches** | Monthly users per app, sign-in on or off per app, the per-app user cap, the session length | A switch takes effect on the test app |
| **A5. Live test** | On `test_claude` with a real Google account the owner signs in with (the owner types the Google password, never the assistant) | Sign-in, sign-out, a second account, a refused forged token |
| **A6. Later (optional)** | Email sign-in or more providers, only if a teacher asks, within the budget | Only on request |

Recommended placement: right after the installable feature, before RAG quality, because students will ask for sign-in quickly once their apps are installable.

## 8. Open points to check before building

1. **Return addresses per app client:** how many a Cognito app client may hold is not confirmed in the docs I checked. If the limit is low for big classes, the platform uses one shared sign-in address (a small bridge page) instead. Checking this is part of A1.
2. **Google's consent-screen rules** (section 5, point 4).
3. **Lite or Essentials:** Lite covers social sign-in at a lower price; Essentials adds passwordless and managed login options we don't need yet. Recommended: **Lite**. Confirm its price on the pricing page at A0.

## 9. Decisions for the owner

1. **Cognito with Google, one pool per app?** Recommended: yes.
2. **Who may sign in by default?** Recommended: any Google account, with a per-app list the teacher can switch on.
3. **Who sets up the Google client?** Recommended: the teacher, once, with a guided page.
4. **Monthly cost guard:** budget alert at $3 a month and a per-app user cap? Recommended: yes.
5. **Session length default?** Recommended: 7 days.
6. **Queue position?** Recommended: right after the installable feature.
