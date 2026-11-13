# Chapter 6: Identity: tokens, sessions, passwords, OAuth, Google sign-in and Cognito

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> Two completely separate sign-in worlds exist here (see `docs/AUTH_PLAN.md` section 0):
> 1. **Platform sign-in**: the teacher's password and the student's token (to manage the platform). Sections A and B.
> 2. **Sign-in for apps students build**: Google/Cognito for the *visitors* of a student's site. Sections C to E.

## A. Authentication vs authorization, tokens and sessions

**Q171 [B] Authentication vs authorization: define both with an example from this project.**
Authentication = who are you? (student token, teacher password). Authorization = what may you do? (a student may publish only their own slug; a teacher may change any student's limits). Most bugs are in authorization (checks that are missing or trust the client).
Study: [Auth0: Authentication vs Authorization](https://auth0.com/docs/get-started/identity-fundamentals/authentication-and-authorization)

**Q172 [B] How does a student prove who they are?**
They paste a token (`mds_...`, 128 random bits). `POST /api/student/login {token}` hashes it (SHA-256), looks up `tok#<hash>`, checks status, and returns a signed session valid 12 h. The token itself is the long-term credential; the session is short-lived.
Repo: `lambda/portal.py` `token_hash`, `sign_session`.

**Q173 [I] Why have a "session" at all if the token is already a credential?**
The CLI/portal doesn't have to send the long-lived token with every request, and sessions can be short, role-specific and revoked by changing the signing secret. The student CLI actually uses the token to obtain a session for the flows that need it.

**Q174 [I] Describe the session format.**
`base64url(JSON{r: role, s: subject, e: expiry}) + "." + base64url(HMAC-SHA256(key, payload))`. The verifier recomputes the HMAC, compares in constant time, checks the role equals the expected one, and the expiry is in the future. It is a simplified JWT-like token (HS256 style) without the header.
Repo: `lambda/portal.py` `sign_session/verify_session`.
Study: [JWT introduction](https://jwt.io/introduction), [RFC 7519](https://datatracker.ietf.org/doc/html/rfc7519)

**Q175 [I] What is HMAC and why is it used instead of a plain hash for signing?**
HMAC mixes a secret key into the hash so only holders of the key can produce a valid tag. A plain `sha256(payload)` can be recomputed by anyone. HMAC also resists length-extension attacks that plain `sha256(secret + data)` suffers.
Study: [RFC 2104](https://datatracker.ietf.org/doc/html/rfc2104)

**Q176 [I] Why do teacher and student sessions use different signing keys?**
`signing_key(role)` derives a distinct key via `HMAC(secret, "<role>-session")`. Even if a student somehow forged a payload with `r: teacher`, the signature wouldn't verify against the teacher key. Defence in depth: role is both in the payload and in the key.

**Q177 [I] Where does the signing secret come from and what happens if it changes?**
A random secret stored with the teacher account in SSM (`TeacherAccount`). Changing it invalidates all existing sessions (a one-step "log everyone out"). It is also backed up/exported in `data export` (`0600` handling).

**Q178 [B] Why are tokens shown only once?**
Only the hash is stored, so the system cannot show it again. If a student loses it, they ask for a regeneration which the teacher approves (a new token replaces the old).

**Q179 [I] What is the regeneration request flow?**
Student: `POST /api/student/regen-request` (only in the last `warnDays` before expiry or after it). Teacher sees pending requests, approves (new duration from now; brings an offline site back) or denies with a reason.

**Q180 [I] What does "extend" vs "approve" vs "online" do?**
Extend adds days to an existing token. Approve replaces a token after a request, counting from now. Online brings a taken-offline site back without changing its expiry (409 if expired or revoked: extend instead). Added in 2.1.1 after a real-account test found there was no way back from offline except extending.

## B. Passwords and the teacher login

**Q181 [B] How is the teacher password stored?**
As a scrypt hash with a random salt (`n=16384, r=8, p=1`, 32 bytes) inside the SSM parameter along with the username and a session secret. Never the password itself.
Study: [RFC 7914 scrypt](https://datatracker.ietf.org/doc/html/rfc7914), [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

**Q182 [I] What is a salt and why a slow, memory-hard hash?**
A salt is random per-password data so identical passwords hash differently and rainbow tables don't work. scrypt costs CPU *and* memory per guess, which hurts GPU/ASIC attackers more than a plain hash does.

**Q183 [I] Why compute the hash even when the username is wrong?**
So the response time doesn't reveal whether the username exists (user enumeration). The code computes the digest first and combines the two comparisons with `&` (no short-circuit).
Repo: `lambda/portal.py` around `scrypt_hash(password, ...)` in the login handler.

**Q184 [I] What is credential stuffing and what stops it here?**
Attackers try leaked username/password pairs. Defences: one teacher account (no email logins), lockout after 5 failures per IP for 15 min, scrypt slowing guesses. Weak points: per-IP only, no MFA (listed in CRITICAL_ISSUES).

**Q185 [I] Why does the installer's `destroy` ask for the teacher login?**
Guards against slips and tricks (a prompt-injected AI cannot type the password; and a wrong stack is caught). It does not stop someone who already holds AWS admin keys (they could delete the stack anyway), which is why a typed confirmation phrase is also required.

## C. OAuth 2.0 and OpenID Connect (needed for Google sign-in)

**Q186 [B] What problem does OAuth 2.0 solve?**
Letting a user grant one application limited access to something at another service without sharing their password (e.g. "this app may read my Google profile"). It is a framework for *delegated authorization*.
Study: [OAuth 2.0 Simplified (oauth.com)](https://www.oauth.com/) and [RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749)

**Q187 [B] What is OpenID Connect (OIDC) and how does it differ from OAuth?**
OIDC is a thin identity layer on top of OAuth 2.0 that adds an **ID token** (a JWT) saying *who the user is* (`sub`, `email`, `name`) and a standard `userinfo` endpoint. "OAuth = access to things; OIDC = login".
Study: [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html), [Okta: OIDC explained](https://developer.okta.com/docs/concepts/oauth-openid/)

**Q188 [I] Name the four roles in OAuth.**
Resource owner (the user), client (the app), authorization server (Google/Cognito), resource server (the API holding the data). In sign-in-only use, the "resource" is the user's identity.

**Q189 [I] Describe the Authorization Code flow in six steps.**
1) App redirects the browser to the authorization server with `client_id`, `redirect_uri`, `scope`, `state`, `code_challenge`. 2) User signs in and consents. 3) The server redirects back with a one-time `code` + `state`. 4) App checks `state`. 5) App's backend POSTs `code` + `code_verifier` to the token endpoint. 6) Gets tokens (ID, access, refresh). The code is useless alone.
Study: [Authorization Code flow](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow)

**Q190 [I] What is PKCE and why do public clients need it?**
Proof Key for Code Exchange: the client invents a random `code_verifier`, sends only its hash (`code_challenge`) first, and the verifier later. A stolen `code` is worthless without the verifier. Needed when the client can't keep a secret (browser/mobile). The platform's app clients are *public clients with PKCE*, so no client secret is stored in an app.
Study: [RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)
Repo: `lambda/auth.py` (`GenerateSecret=False`, `AllowedOAuthFlows=["code"]`).

**Q191 [I] What is the `state` parameter?**
An unguessable value the app generates and checks on return; it binds the response to the user's session and defeats CSRF on the redirect.

**Q192 [I] What is a redirect URI and why must it be an exact match?**
The only address the server will send the code to. If wildcards were allowed an attacker could register a link on your site and steal codes. Each Cognito app client here lists exactly one callback: the app's own `/api/auth/callback`.

**Q193 [I] What are scopes?**
Strings naming permissions asked for (`openid email profile`). The platform requests only those three.

**Q194 [I] What is a JWT and how must a backend verify one?**
A signed JSON token `header.payload.signature`. Verify: signature (using the issuer's public keys from the JWKS URL), `iss` equals the pool, `aud`/`client_id` equals your app, `exp` not passed, `token_use` is what you expect. "Only Cognito's signed tokens are trusted. The backend checks the signature, the issuer, the app client, and the expiry."
Study: [Cognito: Verifying a JWT](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html), [jwt.io debugger](https://jwt.io/)

**Q195 [I] HS256 vs RS256/ES256: what's the difference?**
HS256 = symmetric (same secret signs and verifies; our sessions). RS256/ES256 = asymmetric (private signs, public verifies; Cognito tokens, our KMS kit signature). Asymmetric lets anyone verify without being able to forge.

**Q196 [I] What are access, ID and refresh tokens used for?**
ID token = who the user is (for your app). Access token = permission to call an API. Refresh token = long-lived credential to get new ones without signing in again. The design says the page never sees any token: the backend keeps a session cookie.

**Q197 [I] Why should the browser never hold the tokens in localStorage?**
Any XSS can read localStorage. An `HttpOnly` cookie can't be read by JS. The platform's `npm run check` rule will flag a token saved to local storage.

**Q198 [I] What cookie attributes matter?**
`HttpOnly` (no JS access), `Secure` (HTTPS only), `SameSite=Lax/Strict` (CSRF), a `Path`, and **no `Domain`** attribute so it stays host-only to one student app (Q105).
Study: [MDN: Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie)

## D. Amazon Cognito and Google (what `lambda/auth.py` does)

**Q199 [B] What is Amazon Cognito?**
AWS's managed identity service. *User pools* store users and handle sign-up/sign-in, social login and token issue; *identity pools* hand out temporary AWS credentials (not needed here).
Study: [What is Amazon Cognito?](https://docs.aws.amazon.com/cognito/latest/developerguide/what-is-amazon-cognito.html)

**Q200 [I] Why Cognito instead of writing a sign-in bridge?**
Cognito already does the hard parts: the Google check, codes, token signing, refresh and sign-out. Lower risk and ~$0 up to its free tier (10,000 monthly active users). The platform does not write or maintain a login system, which is the safest posture for a school.

**Q201 [I] What was the "correction found before building" in `AUTH_PLAN`?**
Each Cognito user pool has its own hosted domain and Google requires every such domain registered as a redirect URI on the Google client. One pool per app would force a new Google registration per student app. Fix: **one pool for the whole platform** (one Google registration) with **one app client per student app** (own callback URL).

**Q202 [I] What are the trade-offs of one shared pool?**
Pro: one Google setup; per-app clients are cheap to add/remove. Con: a visitor's Google identity is recognised across all student apps (privacy note needed), and isolation depends on each app checking its own client id and keeping its own records. Owner decision recorded as option A.

**Q203 [I] What does `platform_pool()` do, step by step?**
Reads Google credentials from SSM (fails early if not set up); creates the user pool; creates the pool's Cognito domain `sd-class-<8hex>`; creates the Google identity provider with attribute mapping; saves a flag after each step so a partial failure can resume.
Repo: `lambda/auth.py`.

**Q204 [I] What is the Google redirect address to register, and why `/oauth2/idpresponse`?**
`https://<pool-domain>.auth.<region>.amazoncognito.com/oauth2/idpresponse`. Google sends the user back to *Cognito* (not the app); Cognito then redirects to the app's callback. So Google needs only one registered address for the whole platform.

**Q205 [I] What does `enable_app` create and what does `disable_app` remove?**
Enable: a public app client (`code` flow, PKCE, `ALLOW_REFRESH_TOKEN_AUTH`, token revocation, `PreventUserExistenceErrors`) with callback `https://<slug>.<root>/api/auth/callback`. Disable: deletes only that client; the pool and other apps stay.

**Q206 [I] Why does `student_route` require the backend to be enabled first?**
Sign-in runs inside the student's backend (it exchanges codes and sets cookies), so without a backend there is nothing to call.

**Q207 [D] What ordering problem was found on 2026-11-13?**
The Google registration needs Cognito's domain, but the domain is created the first time sign-in is turned on for an app. So the order is: A2 switch creates the pool/domain and shows the return address → teacher registers it on Google and stores the client in SSM → live test.

**Q208 [I] What is the "sign-in contract" for the AI editor?**
Rules in `AGENTS.md`: never write your own login form/password check/token; the backend decides who the user is via `current_user()`; protect routes with `require_user()` and check ownership; the page never sees tokens; store minimum data; privacy line; sign-out ends the session; limits from the settings registry.

**Q209 [D] How would you verify the token on the student's backend without a library?**
Fetch the pool's JWKS (cache it), pick the key by `kid`, verify the RS256 signature, then check `iss`, `aud`/`client_id`, `exp`, and `token_use`. In practice a vetted library or the platform helper should do it; never hand-roll crypto.

**Q210 [I] What are Google's "unverified app" and consent-screen rules and why do they matter?**
Until the consent screen is published/verified, Google shows a warning to users not on a test list. Basic scopes (email, profile) need only a simple publish. Check Google's current rules before the pilot (AUTH_PLAN section 5).
Study: [Google: OAuth consent screen](https://support.google.com/cloud/answer/10311615)

**Q211 [I] What does Cognito cost and how is it guarded?**
Free for 10,000 monthly active users (Lite/Essentials); beyond that about $0.015 per MAU on Essentials. Guards: a budget alert (80%/100%), a per-app user cap in the settings registry. SMS MFA is not in the budget.

**Q212 [I] What happens to visitor data when an app is deleted?**
Plan: deleting an app deletes its client (and its records); the teacher can erase one visitor's records and delete the user from the pool. Check the current implementation before promising this to users (privacy items are in CRITICAL_ISSUES item 6).

## E. Authentication design questions to think about

**Q213 [D] Where would you add MFA for the teacher and what would it cost?**
TOTP (authenticator app) stored as a secret in the teacher record verified at login: no cost. SMS costs per message and has weaker security. Passkeys/WebAuthn would be the modern best answer.
Study: [WebAuthn guide](https://webauthn.guide/)

**Q214 [D] How would multiple teachers (roles) change the model?**
Add a user table (not a single SSM record), per-teacher password hashes and role claims in the session, an audit log, and per-role authorization in `token_action`. CRITICAL_ISSUES item 15 lists the gap.

**Q215 [D] What attack does the `state`+PKCE+exact redirect combination prevent together?**
Authorization code interception, CSRF on the callback, open redirect token theft, and login CSRF (forcing a victim into the attacker's account).

**Q216 [D] Why must the sign-in cookie be set by the app's backend rather than by Cognito?**
Cognito's hosted domain is a different site from the student's app; cookies there can't be read by the app. The backend exchanges the code server-side then sets a host-only cookie on the app's own domain.

**Q217 [I] Why isolate by *client id* check even in a shared pool?**
Any app client in the pool can mint tokens signed by the same keys. The backend must reject tokens whose `client_id`/`aud` is not its own, otherwise a token from app A would log into app B.

**Q218 [B] If you remember only one thing about auth here, what is it?**
Never invent your own: use signed tokens, constant-time comparisons, slow hashes for passwords, short sessions, exact redirects and PKCE; and always derive identity on the server from a verified credential, never from a value the client sends.

## Study materials for this chapter

- **OAuth 2.0 Simplified** (free book/site): <https://www.oauth.com/>
- **Auth0 Identity Fundamentals**: <https://auth0.com/docs/get-started/identity-fundamentals>
- **Okta: OAuth 2.0 and OpenID Connect overview**: <https://developer.okta.com/docs/concepts/oauth-openid/>
- **Cognito developer guide** (read "User pools", "Hosted UI", "Social IdP"): <https://docs.aws.amazon.com/cognito/latest/developerguide/>
- **Google Identity: OAuth 2.0 for web server apps**: <https://developers.google.com/identity/protocols/oauth2/web-server>
- **Auth0 / Okta / David Neal YouTube** (search): "OAuth 2.0 and OpenID Connect (in plain English)" by Nate Barbettini; "JWT explained" ; "PKCE explained".
- Hands-on: read `lambda/auth.py`, `tests/test_auth.py`, then `docs/AUTH_PLAN.md` sections 2 and 3.
