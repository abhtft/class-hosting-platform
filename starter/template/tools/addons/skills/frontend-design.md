# Frontend design basics

Use this when you build or restyle a page in this project's React site (`frontend/src`). Small, consistent choices make a page look professional. Prefer these over clever effects.

## Layout and spacing
- Pick one spacing scale and use only its steps: 4, 8, 12, 16, 24, 32, 48, 64 pixels. Put the values in CSS variables in `src/styles.css` (for example `--space-2: 8px`).
- Give content a maximum width (about 720px for reading, 1100px for dashboards) and centre it. Never let text run across a whole wide screen.
- Group related things closely and separate groups with more space. Space shows what belongs together better than borders do.
- Align things to one edge. Mixed left and centre alignment on one page looks accidental.

## Type
- One font family for the whole site (two at most). Use a scale of sizes, for example 14, 16, 20, 24, 32, 40, and keep body text at 16px or larger.
- Line height about 1.5 for paragraphs, 1.2 for headings. Keep lines under about 70 characters.
- Use weight (regular, medium, bold) to show importance, not many different sizes.

## Colour
- Choose one main colour (the accent), plus neutral greys for text, borders and backgrounds. Use the accent only for the main action and for links.
- Text must be dark enough on its background (see the accessibility skill for the contrast rule). Do not use light grey text for anything the student needs to read.
- Define colours once as CSS variables and reuse them. Support dark mode only if the student asks for it; if so, change the variables, not every rule.

## Components
- Buttons: one primary button per view; others look quieter. Give every button a clear verb ("Save notes", not "OK").
- Cards: same padding, same corner radius, same shadow everywhere. Do not mix styles.
- Forms and lists: see the forms and responsive skills.

## States people forget (always build them)
- **Loading:** show a short message or a simple placeholder, never a blank area.
- **Empty:** say what will appear here and what to do next.
- **Error:** say what went wrong in plain words and how to try again. Never show raw error text or a stack trace to visitors.
- **Disabled:** make it visible and explain why when it is not obvious.

## Keep it simple
- Start with the plain version, using the page structure and `src/styles.css` that already exist. Add a library only if the student asks for something the plain version cannot do well.
- Change one thing at a time and look at the result in the browser before the next change.
