# The short interview (for the AI assistant)

Read this when the student starts a NEW app or asks for a big change. The goal is a clear one-page plan the student has agreed to,
before any code. A few good questions now save the student many rounds of "no, not like that" later.

## How to run it

1. **Skip it** when the student already gave a detailed description ("a quiz with 10 questions about space, dark theme, score at the end") or says "just build it". Then write `SPEC.md` from what they said, show it in a few lines, and go.
2. **At most two rounds**, one message each, **3 to 6 questions** per round, in plain words a school student understands. Number them. After the first round, ask a second only if something important is still unclear.
3. For every question give **choices or an example and a default**: "Who is it for? (a) me and friends (b) my class (c) anyone online. If unsure I'll assume (b)." Never ask an open "what do you want?" alone.
4. **Never ask about technology** (React, Python, hosting, databases). The platform decides that. Ask what the visitor sees and does.
5. **Check which features are on** (the `status` tool or `npm run deploy -- status`) before you promise anything that needs the backend, class AI or class storage. Ask only about features that are on; for an off feature say plainly that your teacher must switch it on and what you will build without it.
6. **Say early what the platform cannot do**, so the student does not plan around it: taking payments, sending emails, login with passwords for other people, a live chat between visitors, anything that needs a server running all the time (see "Does not work here" in `AGENTS.md`). Offer the closest thing that works (for example "contact me to order" instead of a checkout).
7. Then write **`SPEC.md`** in the project (the format is below), show the student a short summary, and ask **"Is this right? Anything to change?"** Build only after a yes. Keep `SPEC.md` up to date when the plan changes; it is the student's own file, not a platform file.

## Questions every app needs (pick what is not already answered)

- **Who will use it?** (just me / my class / anyone online) And in which language?
- **What is the one thing a visitor should be able to do in the first 10 seconds?**
- **Which pages or screens?** (suggest 2 to 4 based on the idea; let the student cross out or add)
- **Does it need to remember things between visits?** (nothing / only on this device / for everyone who visits). "Only on this device" is `localStorage`; "for everyone" needs class storage, if it is on.
- **Does it need the AI?** If it is on: what should the AI do, in one sentence, and what must it refuse or avoid?
- **What does it look like?** Three words ("calm, dark, playful"), or a site they like, plus a main colour. Do they have their own text, photos or logo?
- **Is there anything it must not do or show?** (Nobody's private data; no real names or photos of other people without asking them.)

## Extra questions by kind of app

Pick the closest kind. If none fits, use only the questions above.

| Kind | Also ask |
|---|---|
| **Personal or portfolio site** | What should people learn about you in one line? Which 3 to 6 things to show (projects, skills, photos)? Contact: show an email text, a link, or none? |
| **Shop or small business** (no payments here) | What do you sell and how many items? For each item: name, picture, price, short text? How do people order (message you, a phone number, an email text)? Opening hours or location? |
| **Event or invitation** | Date, place, what to bring? Do guests answer "I'm coming" (needs class storage; ask who may see the list)? A countdown or map link? |
| **Quiz, game or learning tool** | Which topic and age? How many questions or levels? Fixed questions you write, or made by the AI (needs class AI)? Score, timer, a "play again" button? What happens at the end? |
| **Notes, blog or journal** | Who writes (only you or visitors)? Does it need a title, date, tags? Should entries be private to the device or shared? |
| **Tracker or dashboard** | What do you record (for example hours, money, habits)? What fields per entry? What should the page show: a list, totals, a chart? Where does the data live (this device or shared)? |
| **Form or survey** | The questions and the type of each answer? Who sees the answers (only you)? What is the thank-you message? Do you ask for personal data? (Prefer not to.) |
| **AI helper or chatbot** (needs class AI) | Its name and personality? Who is it for? What should it never do or talk about? Short or long answers? Does it remember the conversation (only while the page is open)? |
| **Story, presentation or report** | How many sections or slides? Text only, or pictures too? Navigation: scroll, buttons, keyboard? Should it be easy to print? |

## Writing `SPEC.md`

Keep it under one page, in the student's words:

```markdown
# <name of the app>

**For:** <who>   **Language:** <language>
**In 10 seconds a visitor can:** <the main action>

## Pages
1. <page>: <what is on it>
2. ...

## Remembers
<nothing / on this device (localStorage) / for everyone (class storage: yes/no is on)>

## AI
<what it does and what it refuses, or "none">

## Look
<three words, main colour, text and pictures the student will provide>

## Not included (yet)
<things we agreed to leave out, and why>
```

## While building

- Build the first version small, show it (`npm run dev`), and ask what to change. Do not add pages or features that are not in `SPEC.md` without asking.
- When the student asks for something new, say whether it fits the platform; if it changes the plan, update `SPEC.md` first.
- If a question was never answered, use the default you offered and say so in the summary.
