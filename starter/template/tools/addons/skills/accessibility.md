# Accessibility checklist

Use this for every page you build here. A page that works for keyboard users, screen readers and low-vision visitors also works better for everyone else. Check the list before you tell the student the page is done.

## Structure
- Use real elements: `button` for actions, `a` for links, `nav`, `main`, `header`, `footer`, `h1` to `h3` in order. Do not make a clickable `div`.
- One `h1` per page. Headings go down one level at a time (h1, h2, h3).
- Set the page language on the `html` element and give the page a clear `title`.

## Keyboard
- Everything that can be clicked can be reached with the Tab key and used with Enter or Space.
- Never remove the focus outline unless you replace it with a clearly visible one.
- Tab order follows the visual order. Do not use positive `tabindex` values.
- A popup or menu must close with the Escape key, and focus returns to what opened it.

## Text and colour
- Contrast: normal text needs at least 4.5 to 1 against its background; large text (24px, or 19px bold) needs 3 to 1. Check the accent colour on white and on the page background.
- Do not use colour alone to show meaning. Add an icon or a word next to red and green states.
- Let people zoom: no fixed pixel heights on text containers, and do not block pinch zoom.

## Images and media
- Every meaningful image has short `alt` text that says what it shows or what it does. A purely decorative image gets `alt=""`.
- An icon-only button needs a text label for screen readers (`aria-label`).
- Video and audio need captions or a transcript.

## Forms
- Every input has a visible label connected to it (`label` with `htmlFor`). A placeholder is not a label.
- Show errors next to the field, in words, and name the field in the message.
- Mark required fields in the label text, not only with a colour or a star.

## Motion and updates
- Respect reduced motion: wrap animations in the `prefers-reduced-motion` media query and turn them off or soften them.
- When content changes without a page load (a result appears, a save finishes), tell screen readers with a live region (`aria-live="polite"`).

## Quick test before finishing
1. Put the mouse aside and use only the keyboard on the whole page.
2. Zoom the browser to 200 percent. Nothing should be cut off or overlap.
3. Read the page text with styles off in your head: does the order still make sense?
