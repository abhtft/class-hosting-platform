# Responsive layout (phones first)

Use this so a page works on a phone, a tablet and a laptop. Most visitors of a class project open the link on a phone, so design that view first and widen it second.

## Method
1. Build the narrow (phone) layout first: one column, full width, comfortable spacing.
2. Add `min-width` media queries only where the layout needs more room. Typical points: 600px, 900px, 1200px. Add a point when the design breaks, not because a device exists.
3. Always have the viewport tag in `index.html`: width equals device width, initial scale 1.

## Layout tools
- Use Flexbox for rows of items and CSS Grid for page areas and card grids.
- A card grid that adapts without media queries: `grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))` with a `gap`.
- Use `max-width` plus `width: 100%` on containers instead of fixed widths. Use `rem` or `%` for sizes, not fixed pixel widths on large blocks.
- Images and video: `max-width: 100%` and `height: auto` so they never overflow.
- Use `min-height: 100dvh` for full screen sections, not `100vh`, so mobile browser bars do not cut the page.

## Touch
- Buttons and links need a touch area of at least 44 by 44 pixels, with space between them.
- Do not depend on hover. Anything shown on hover must also work with a tap or with the keyboard.
- Form inputs use at least 16px text on phones, otherwise some phones zoom in on focus.

## Content
- Tables: let them scroll sideways inside a wrapper (`overflow-x: auto`) or turn rows into stacked cards on small screens.
- Navigation: a few links can wrap; a long menu becomes a button that opens a list. Keep the main action visible.
- Long words and URLs: allow them to wrap (`overflow-wrap: anywhere`).

## Check before finishing
1. Open the browser developer tools, choose a phone size (about 375px wide) and scroll the whole page. There must be no sideways scroll on the page itself.
2. Try 768px and a wide desktop. Check that the line length stays readable.
3. Rotate the phone view. The layout still works.
