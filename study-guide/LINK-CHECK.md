# Link check (2026-11-13)

Method: every `https://` address in this folder (238 unique) was requested with `curl` (follow redirects, 20 s timeout, browser-like user agent; YouTube videos checked through YouTube's oEmbed endpoint).

| Result | Count | Meaning |
|---|---|---|
| 200 OK | 230 | The page answered. (A 200 does not prove the page is still about the same topic; skim before relying on it.) |
| Not reachable from my machine (timeout) | 2 | `https://codahale.com/a-lesson-in-timing-attacks/` and `https://nlp.stanford.edu/IR-book/`. Both are long-standing, well-known pages; open them in a browser. |
| Placeholders, not links | 2 | `https://<slug>...` and `https://<pool-domain>...` in explanations. |
| Failed or blocked, then fixed | 4 | A 404 npm page (replaced), two O'Reilly pages that block bots (links removed, book titles kept), one ScienceDirect page (replaced by a search phrase). |

Not checked: the video titles given as "search: ..." phrases (no fixed URL), and whether each page still says what the guide says it says.

If a link breaks later, search for the page title shown in the link text.
