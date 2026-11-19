# Students signing in with Google (version 2.11.0)

Until now a student signed in with a **token** the teacher made and sent. That still works, unchanged, and is the default. This adds an **optional** second way: the student signs in with their Google account, sees their sites and the class projects, asks for what they need, and the teacher answers. Design and reasons: [STUDENT_IDENTITY_PLAN.md](STUDENT_IDENTITY_PLAN.md). Routes: [API_REFERENCE.md](API_REFERENCE.md) section 8.

## What it gives

- **No token to copy.** A site made through a request has no token to hand out. Each computer is *paired* with a short code, like signing in to a TV, and gets its own personal token (`mdp_…`) that works for 30 days and can be signed out one computer at a time. Your old tokens (`mds_…`) are not affected.
- **A home for the student.** Their sites; the class projects (green = their site is in it, red = it is not, with *Request access*); their requests and your answers.
- **Requests.** A student asks for a new site with the settings they expect (how long, which features, a note), or asks to join a class project. You **accept**, **accept with changes** or **reject**. Nothing is granted until you answer.

## Teacher: turning it on (once)

1. The shared sign-in pool and the Google client must exist (see [SIGN_IN.md](SIGN_IN.md)). Update the platform (`plan`, show it, `apply <plan id>`): this release adds one permission, `cognito-idp:UpdateIdentityProvider`, which puts Google's "e-mail is verified" fact into the sign-in token for a pool made earlier.
2. In the teacher page open the **Google** tab, **Who may sign in…**, choose a mode and give your portal address:
   - **Off:** tokens only (the default).
   - **Invite:** only the listed e-mail addresses.
   - **Domain:** the listed addresses and everyone at a listed domain (for example your school).
   - **Request:** listed people are in; anyone else with a verified Google account may sign in and *ask*, and gets nothing until you approve.
3. To show a team project to students, open it in the **Projects** tab and choose *List in the class catalogue*.
4. **Put people on a project by e-mail.** Open a project's card, then **People**, **Add people…**, and type one or more e-mail addresses. Someone who has already signed in becomes a member at once. The others get an invitation and become members at their first sign-in with Google; an invited address is let in even if it is on no list (in Invite mode you do not have to list it twice). Each person pairs their own computers and has their own token, so **Remove** stops that person's computers at once and nobody else's. Your own token for the project (the old one) is unchanged. A project is the same thing as a site: one web address, one backend, one AI budget, shared by its people.

## Student

Open the portal's student page, **Sign in with Google**. To use a computer: in the project folder run `npm run login -- --google`, then type the code it shows into **Pair a computer** on the page. Only do this for a computer you are sitting at.

## Rules that keep it safe

- Only Google's signed token is trusted, and only the portal's own sign-in client (a token made for a student app is refused). The e-mail must be verified by Google. An address is matched exactly (a look-alike domain does not match).
- Your rules and the *turn off* switch apply to open sessions **at once**: removing an address, turning a student off, or turning Google off stops their Google sessions and their paired computers on the next request.
- A computer sends the platform only a **hash** of its token; the token never travels until it signs in. A session from a paired computer ends the moment the computer is signed out, and never outlives the pairing.
- Limits (setting group *Student sign-in with Google*): 3 waiting requests per student, 10 requests a day, 5 sites per account, 5 computers per person and project, 50 people on a project, 200 waiting requests in the whole class, 30-day personal tokens, 10-minute codes.
- A token you made and the student linked to their account keeps working whatever the Google settings are. Turning Google off brings the platform back to how it was; a site made *for* a Google account can then only be used after you turn it on again.

## Privacy

The platform stores, for each student who signs in: Google's account id, e-mail address and name, when they first and last signed in, and their requests. Tell your students. The **Erase…** button on the Google tab removes the account, its requests and its paired computers; its sites are only unlinked (sites made for the account are switched off), and you delete a site with the usual controls.

## Not included

E-mail notifications (the Google tab shows a badge), automatic approvals, other sign-in providers, merging two accounts.
