# Project homes and people on a project (version 2.11.0)

The teacher and the student each have a **home** made of project cards. A project can have several **people**, put there by e-mail, and each person has their own computers and their own sign-in to it. This document is the final record of that feature. The sign-in itself (Google, requests, pairing) is in [STUDENT_IDENTITY.md](STUDENT_IDENTITY.md); the routes are in [API_REFERENCE.md](API_REFERENCE.md).

## The idea in four lines

- A **project is a site**: one web address, one backend, one AI budget, shared by its people.
- A **student** is a person with a Google account. The teacher keeps the username and password login.
- A person is put on a project by **e-mail**. They need no token from the teacher.
- Taking one person off stops **only that person**; the others and the teacher's own token are unaffected.

## The two homes

| | Teacher (`teacher.html`) | Student (`google.html`) |
|---|---|---|
| First card | **New project** | **Request new project** |
| Then | One card per project with status, expiry, last publish, features | The student's projects as cards; waiting and earlier requests below |
| Tools | Search, filter (all, active, ending soon, expired, no activity, waiting), sort | The same cards, without teacher controls |
| Detail | Click a card for the panel: **People**, **Features**, token controls | Click a card for its link, computers and the project's people count |

The card logic is in `web/cards.js` (filter, sort, activity) and is covered by `tests/test_google_pages.mjs`.

## People on a project

Open a card, then **People**, **Add people…**, and type one or more addresses (separated by commas, spaces or new lines).

| What happens | When |
|---|---|
| The person becomes a **member** at once | They have already signed in with Google |
| The address is stored as an **invitation** and they join at their first sign-in | They have not signed in yet |
| The address is **already there** | Adding twice changes nothing |
| The address is **refused**, with the reason | Not a valid address, the account is turned off, or the project is full |

Rules:

- The address is matched **exactly, in lower case**. Only a **verified** Google e-mail counts. There is no guessing about dots or plus signs, so a look-alike address does not match.
- An invited address is let in **even if it is on no list** (in Invite mode you do not list it twice). The invitation is one record per address (`inv#…`) holding the projects it was invited to.
- **Remove** takes a member off, deletes that person's paired computers for the project and revokes them on their next request. **Withdraw** removes a waiting invitation.
- The limit is **50 people per project** (`identity.peoplePerProject`). Computers are limited **per person and project** (5), so one person's computers never use up another's.
- When one member's account is turned off, the project keeps working for the others. When an account is erased it is taken off every project.
- Only the teacher can see or change who is on a project (routes below). A project nobody was added to behaves as it always did.

## Existing sites

Nothing is rewritten. A site made before this feature is a project with nobody added (lazy migration): its token keeps working. A project gets `members`, `invites` and `owner_sub` the first time someone is added.

## Routes

| Route | Who | What |
|---|---|---|
| `GET /api/admin/tokens/{id}/people` | teacher | Members, invitations and the limit |
| `POST /api/admin/tokens/{id}/people` | teacher | Add addresses; returns added, invited, already, refused |
| `POST /api/admin/tokens/{id}/people/remove` | teacher | Remove by `sub` or withdraw by `email` |

## Where it lives

`lambda/access.py` (members, invites, routes), `lambda/identity.py` (invitation at sign-in), `lambda/portal.py` (dispatch, `check_owner`), `web/cards.js`, `web/people.js`, `web/teacher.js`, `web/google.js`. Tests: `tests/test_people.py` (and the cards and People checks in `tests/test_google_pages.mjs`).

## How it was checked

The suite passes. On the live platform the owner checked: the teacher and student homes, Google sign-in, people and invitations, pairing a computer from a project, removal, and `npm run login -- --google`, `npm run pull`, `npm run status` in a fresh copy of the kit. The old token for `test_claude` still signs in.

## Not included (see [FUTURE.md](FUTURE.md) section 14)

A catalogue of "visible to all" project names for people not yet admitted, a direct *Request access* without owning a site first, tokens shown again with Reveal, and automatic pull-and-merge before a publish.
