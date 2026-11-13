# Sign in with Google for the apps your students build

Kit 2.9.0. This is about the **visitors of a student's app**: people who open the app and need to be recognised (their own notes, a private page, a score list). It does not change how teachers and students sign in to the platform itself. The plan and the reasoning are in [AUTH_PLAN.md](AUTH_PLAN.md).

## What it is

- One **Amazon Cognito user pool** serves the whole platform, with one **Google registration**. Each student app gets its own client in it, with its own return address.
- The app's backend (`class_auth`, in Python and Node) sends the visitor to Google, receives them back with a one-time code, exchanges it over HTTPS, checks the answer, and then sets **its own signed session cookie**. The page never sees a token and nothing is kept in the browser's storage.
- The cost is **$0** for a class: Cognito's free tier covers 10,000 monthly users per account, and Google sign-in is free. Above that, Cognito charges per monthly user (see its pricing page).
- Nothing about a visitor is stored except a stable id, the email and the name, and only inside the app's session cookie; the student's app decides what to save under that id.

## For the teacher: set Google up once for the whole class

You need a Google account and about ten minutes. Everything on Google's side is done by you in your browser; nobody else needs the Google secret.

1. **Get the return address.** In a student project, run `npm run signin`. The last lines show the **Google return address** (it ends with `/oauth2/idpresponse`). It is the same for every app. (It needs one student's backend to be on.)
2. **Create the Google client.** In the Google Cloud console create a project, set up the **OAuth consent screen** (app name, your support email; scopes `openid`, `email`, `profile`), then **Credentials, Create credentials, OAuth client ID, Web application**. Under **Authorised redirect URIs** add the return address from step 1. Copy the **client ID** and **client secret**.
3. **Keep to Google's rules for who may sign in.** While the consent screen is in "Testing", only the test users you list can sign in (up to 100). To let anyone sign in, publish the consent screen. Google's rules about publishing change, so read what Google's console says for an app that asks only for name and email.
4. **Store the client once**, as a secure parameter in your AWS account, named `/student-deploy/auth-google`, with this JSON value (use your own values; do not paste them anywhere else):

   `{"client_id": "...apps.googleusercontent.com", "client_secret": "..."}`

   In the AWS console: Systems Manager, Parameter Store, Create parameter, type **SecureString**. (The parameter name is the platform setting `install.authGoogleParameter`.)
5. The platform creates the pool and the Google connection the first time a student turns sign-in on. If you have not stored the client yet, turning it on stops with a plain message and creates nothing.

## For the student: turn sign-in on for your app

1. `npm run signin` shows whether sign-in is on. It needs your teacher to have switched your **backend** on.
2. `npm run signin -- on` turns it on for your app. It prints the address visitors come back to.
3. `npm run deploy` so your page and backend are current. The starter already has the sign-in bar (`AuthBar`) and the example route `/api/private`.
4. Open your site and press **Sign in with Google**. After signing in the bar shows your name and a **Sign out** button.
5. `npm run signin -- off` turns it off again (visitors can no longer sign in).

On your own computer (`npm run dev`) a pretend visitor ("Local Visitor") signs in at once, so you can build without Google. The project checker (`npm run check`) warns when a project has a login of its own: use the platform's sign-in instead.

## For the AI editor

The kit's rules (AGENTS.md, "Sign-in") tell the AI to use `class_auth` and `useUser()`, never to write a login, to take the user only from `current_user(event)` (never from the page), to save each visitor's data under their id and check it on every read and write, and to ask the student before turning sign-in on.

## What a visitor sees and what is kept

- They press **Sign in with Google**, choose their account on Google's page, and come back to the app signed in.
- The app keeps a session cookie (default **7 days**, set by the platform setting `auth.sessionDays`; a visitor can sign out any time). The cookie holds the visitor's id, email and name, signed so it cannot be changed.
- Because one pool serves the class, the same Google account is recognised across the class's apps. Each app only sees its own visitors' data.
- Tell your visitors in the app what you save about them and who to ask to remove it. The teacher can remove an app's sign-in clients and, with them, new sign-ins.

## If something goes wrong

| What you see | What it means |
|---|---|
| `npm run signin -- on` says "Google sign-in is not set up yet" | The teacher has not stored the Google client (teacher step 4). Nothing was created. |
| It says "Sign-in needs your backend" | Your teacher must switch your backend on first. |
| Google says the redirect address does not match (`redirect_uri_mismatch`) | The Google client does not list the return address from `npm run signin` (teacher step 2). |
| Google says the app is not verified, or you are not a test user | The consent screen is in Testing and your account is not on the list, or it needs publishing (teacher step 3). |
| The page returns with `?signin=failed` | The sign-in did not complete (it took longer than ten minutes, or the answer was not valid). Try again; nothing was signed in. |
| `/api/auth/me` says `sign_in: "off"` | Sign-in is off for this app: `npm run signin -- on`. |
