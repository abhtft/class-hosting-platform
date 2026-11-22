# Consistency of a version (conflicting facts)

**Problem (found 2026-11-22).** The same fact (the opening hours) lives in several places of a version: the instructions, the fixed fallback and hand-over sentences, the inbox notes, the media descriptions and, later, the corrections ("extras") a client taught. A correction changes only what the model writes; a fixed sentence never changes. So the owner taught "10 AM to 6 PM" and the customer still got "9 AM to 6 PM" in the hand-over sentence. The books are embedded, so they are not edited here.

**Decision (owner, 2026-11-22).** Scan, then propose, then the owner approves. No automatic edits.

1. **Scan.** One model call over the version's editable texts and its corrections: it lists each place that disagrees with a correction (the newest correction wins) or with another text, as `{field, quote, fix, fact, why}`. A quote must be literally in its field or it is dropped.
2. **Propose.** Studio shows each change (what it says now, the suggested text, why) and the owner applies or ignores it; applying changes only the editor's draft.
3. **When.** Every Publish runs the scan first (the owner may publish anyway), and Build has a "Check for conflicts" button. A version made from a teach session lands in the editor, so the same scan covers it.

**Not done on purpose.** A single "business facts" card with placeholders (the longer-term fix) is deferred until the scan shows how often facts really clash.

**Cost.** One model call of a cent or less per scan, counted against the business's month. Route: `POST /admin/agents/{name}/consistency` with `version` or `config`.
