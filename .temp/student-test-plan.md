# Student test plan (as a real student, before any new feature)

Goal: find what a student actually hits. You play the student, I watch the terminal and the AWS side. Private file (.temp is git-ignored).
Time: about 90 minutes. Use your live platform (417780655868, profile `default`) and test students only.

Rule for every test: write down (a) how long it took, (b) anything confusing, (c) any error text, word for word. Confusion is a result too.

## 0. Before you start (10 minutes)

0.1 Make sure the platform is on the newest release so the fixes are live (the Bring online button, timeouts in errors-only logs, the kit `.gitignore`). **Checked just now: your live portal already serves kit 2.1.1, so it looks updated.** Confirm with this plan; it should say "Nothing would change". If it instead lists changes, read them: they must say "removes 0" and "replaces no data". In a terminal:

```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v2.1.1/class-platform-teacher-2.1.1.tgz plan --profile default
```
Read the plan. It must say "removes 0" and "replaces no data". If it lists any Remove, stop and tell me. Otherwise apply it with the plan id it prints:

```bash
npx -y https://github.com/abhtft/class-hosting-platform/releases/download/v2.1.1/class-platform-teacher-2.1.1.tgz apply <plan id> --profile default
```

0.2 Make two NEW test students on the teacher page: `stu-a` (30 days) and `stu-b` (30 days). Copy each token. Switch on for both: Backend, Class storage. (Class AI only if your OpenRouter or Gemini keys are saved: `npx -y <package> secrets --profile default`.)

0.3 Make a brand-new empty folder for each, for example `D:\Github_Codes\stu-a` and `D:\Github_Codes\stu-b`. Do NOT reuse `claude_test`: it was set up by hand.

0.4 Pick the AI editors. Use at least TWO different ones across the two students (for example Claude Code for `stu-a`, and Cursor, Copilot, Windsurf or Gemini for `stu-b`).

## Part 1: first contact (the most important part)

**T1. Sign in and follow "Start here"** (student `stu-a`)
- Open the portal home page, sign in with the token. Do not read any docs first: do only what the page says.
- PASS if you reach a live site without help. Note every point where you hesitated.
- Record: minutes from sign-in to "I know what to do next".

**T2. Connect the AI editor and set up the project**
- Follow the Start here card to connect your editor, then say exactly: `set up my class project`.
- PASS if a project folder appears with `frontend/`, `backend/`, `AGENTS.md` (and the editor's rules file), and the editor knows the platform's rules.
- Check: `npm run platform -- status` shows the kit signed ("signed by your teacher's key") and up to date.

**T3. Log in once**
- `npm run login`, paste the token. PASS if it says "Signed in as ...".
- Try a wrong token too (change one character): the message must say what to do, not show a stack trace.

**T4. The interview** (this is the feature never tried live)
- Say exactly: `I want to make an app for my class`.
- PASS if the editor asks 3 to 6 numbered, plain questions with choices and a default, never asks about technology, then writes a one-page `SPEC.md`, shows a short summary and asks "Is this right?" BEFORE writing code.
- Then try the other branch with a second student/editor: `Just build it: a quiz about space with 10 questions, dark theme`. PASS if it skips the interview and writes `SPEC.md` from your words.
- Note which editor followed the rules and which ignored them.

**T5. First publish**
- Say `publish my site`. PASS if the editor asks for confirmation, publishes, and gives you the live address. Open it on your phone too.
- Check the editor never prints the token and never asks you to paste it into chat.
- Record: minutes from "set up" to live site (target: under 15).

## Part 2: building and changing

**T6. A change goes live**
- Ask for one visible change (a new colour or a sentence). Publish again. PASS if the new look shows on a normal refresh within a minute.

**T7. A backend route**
- Say: `add a page where I can type a note and it is saved`. PASS if it builds a backend route and uses class storage, the note survives a reload, and a second device sees it ("for everyone" storage).
- Try a very long note (300+ characters). PASS if you get a friendly message, not a crash.

**T8. When the backend breaks**
- Introduce an error on purpose (ask the editor: `make the notes route crash`). Open the live page.
- PASS if the page shows a clear message, and you (or the editor) can find the cause with `npm run logs -- --errors` or the editor's `logs` tool without any extra explanation from me.
- Also open the portal's Backend card: the Errors only box and the time choice should match the command-line output.

**T9. A secret**
- In the portal add an environment variable (`TEST_SECRET`, value any made-up text), make the backend print it, read `npm run logs`. PASS if it shows `***`, never the real value.
- Ask the editor to "put my API key in the code": it should refuse and point to the environment variables.

**T10. Class AI** (only if keys are saved and AI is on for the student)
- Say `add a button that asks the AI a question`. PASS if it works with no key in the student's code, and a message appears when the cap is reached (set a tiny cap such as $0.01 to see it).

## Part 3: when things go wrong for the student

**T11. Timeout and limits**
- Ask for a route that waits 40 seconds. PASS if the page shows "The backend took too long to respond" and `npm run logs -- --errors` shows the timeout line (new in 2.1.1).

**T12. Site taken offline and back**
- On the teacher page, take `stu-a` offline. As the student, try to publish. PASS if the message is clear.
- Click **Bring online** (new in 2.1.1). PASS if the site is back with the same expiry date. Also test **Extend**.

**T13. Token about to expire: the student asks for a new one**
- On the teacher page set `stu-b` to expire in 1 day (Extend with a small number or create a short token), then, as the student, use the request-regeneration flow. PASS if the student can ask and you can approve it on the Requests tab and the student continues.

**T14. Kit update reaches the student**
- (Only if a newer kit exists.) Run `npm run deploy` in an older project. PASS if it updates itself, prints what changed, and never stops the student. Refused kits print a plain reason and the deploy still continues.

**T15. Wrong editor, wrong folder**
- Run `npm run deploy` in a folder that is not a project. PASS if it says what is wrong in one plain sentence.

## Part 4: the teacher side while students work (do these during Part 1 to 3)

**T16. Usage tab** (never looked at live)
- After the students made some requests, open the **Usage** tab. PASS if the runs and spend match what you did (for example about N runs used), and the "most the AI can cost" matches the caps you set.
- Lower a student's run allowance to just above what they used. PASS if they move to "near a limit".

**T17. Clear data**
- On a student's class storage press **Clear**. PASS if the notes are empty afterwards and the student can still write new ones.

**T18. Revoke**
- Revoke `stu-b`'s token. PASS if their next `npm run deploy` says the token is revoked, and features are locked.

## Part 5: results table (fill in as you go)

| # | Test | PASS/FAIL | Minutes | Anything confusing or any exact error text |
|---|---|---|---|---|
| 0.1 | update to 2.1.1: plan removes 0 | | | |
| T1 | sign in and Start here | | | |
| T2 | connect editor, set up project | | | |
| T3 | login, wrong token | | | |
| T4 | interview (and skipped interview) | | | |
| T5 | first publish | | | |
| T6 | a change goes live | | | |
| T7 | backend route and storage | | | |
| T8 | break it, find the cause with logs | | | |
| T9 | secret masked, refuses key in code | | | |
| T10 | class AI (optional) | | | |
| T11 | timeout message and logs | | | |
| T12 | offline, Bring online, Extend | | | |
| T13 | regeneration request | | | |
| T14 | kit update | | | |
| T15 | wrong folder | | | |
| T16 | Usage tab | | | |
| T17 | Clear data | | | |
| T18 | revoke | | | |

## What to send me

- The filled table, plus the exact text of every error and every moment you were unsure what to do.
- Which editor you used for which student, and which rules it followed or ignored.
- A screenshot of the Usage tab and of the portal Backend card.

Then I turn every FAIL and every confusing moment into a test first, then a fix, and we release 2.1.2. No new features until this is done.

## At the end

Revoke both test tokens (and the old `test_claude` one). Check nothing real was left: `aws s3 ls s3://<sites bucket>/sites/ --profile default`.
