import os
os.chdir(r"D:\Github_Codes\class-hosting-platform-github")


def edit(p, a, b):
    s = open(p, encoding="utf8").read()
    assert a in s, (p, a[:50])
    open(p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("CHANGELOG.md", "Not built yet: replay of stored questions",
     "**Daily summaries and alerts (T6):** every night the platform writes the numbers of the day before (answers, how many said the books do not say, answers with an error, tokens, cost, model time; for the class and for each student) as small items kept 400 days, from the trace summaries only (no extra cost to speak of). The Traces tab shows the last 14 days in a small table (today live). With the alert email set up, you get one email a day at most when the share of \"the books do not say\" is 15 points above the previous week's (at least 20 answers), or when 20% of answers had an error. New route `GET /api/admin/traces/summary`; the nightly sweeper may write and read its own `r#` items. Not built yet: replay of stored questions")
edit("docs/TRACEABILITY_PLAN.md", "T6 is not built.", "T6 (daily numbers, the table on the Traces tab and the change alert) is built and tested locally, not yet published. This completes the planned traceability steps except replay and the Phoenix file, which are optional.")
edit("docs/TRACE_SCHEMA.md", "## 8. Guarantees the tests check", """## 7d. Daily numbers and alerts (T6)

Each trace summary also counts `refused` (the app's `answer` step said the books do not say) and `llm_ms` (model time). A nightly job reads the day's summaries (index query, no step reads) and writes one item `r#<day>` / `all` (kept 400 days) with `answers`, `refused`, `errors`, `tokens`, `cost_micro`, `llm_ms` and the same per student under `apps`. `GET /api/admin/traces/summary?days=14` returns the last days (today computed live). The alert (alert email only, at most one a day) fires when, with at least 20 answers, the refusal share is 15 points above the previous 7 days' share, or 20% of answers had an error; it is checked for the whole class and for each student.

## 8. Guarantees the tests check""")
