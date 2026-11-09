# Forms and validation

Use this whenever the page collects input: sign-up, feedback, a question for the AI, a quiz. A clear form gets more complete and more correct answers.

## Design
- One column. Label above the field. A visible label for every input (never only a placeholder).
- Ask only for what you need. Remove fields you will not use.
- Group related fields under a short heading. Put the main button at the end, with a verb on it ("Send feedback").
- Use the right input type: `email`, `number`, `date`, `tel`, `url`, and `textarea` for longer text. Set `autoComplete` where it fits.
- Show limits next to the field ("Up to 500 characters") and keep a live count for long text.

## Validation
- Validate in two places: in the page, for quick help, and in the backend, because the page can be bypassed. Never trust only the page.
- Check when the student leaves a field, or when they submit, not on every key press. Do not show an error before they had a chance to type.
- Write the message so it says what to do: "Enter your email address, with an @ and a domain", not "Invalid input".
- Show the message next to the field, mark the field, and move focus to the first field with a problem after a failed submit.
- Keep what the visitor typed when an error happens. Never clear the form on failure.

## Submitting
- While sending: disable the button and change its text ("Sending..."), so it cannot be sent twice.
- On success: say so clearly, and either reset the form or show the result.
- On failure: show a plain message ("Could not send. Check your connection and try again.") and keep the data. Do not show raw error text.
- A request that takes longer than a few seconds (for example the AI) needs a visible progress message.

## Safety
- Treat all input as text. Never put it into the page as raw HTML.
- Do not ask for passwords, bank details or other sensitive data in a class project.
- Limit the size of text accepted by the backend, and check type, length and allowed values there.
- Be careful with personal data: collect the least, and tell the visitor what it is used for.

## Accessibility
- Connect labels to inputs, mark required fields in the label, and read the accessibility checklist for errors and focus.
