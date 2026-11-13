import os
os.chdir(r"D:\Github_Codes\class-hosting-platform-github")
p = "lambda/traces.py"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:60]
    s = s.replace(a, b, 1)


rep('''        if kind == "llm":
            adds["llm_ms"] = max(int(dur_ms or 0), 0)''', '''        if kind == "llm":
            adds["llm_ms"] = max(int(dur_ms or 0), 0)
            adds["llm_calls"] = 1
        if kind == "retriever" and isinstance(attrs, dict) and attrs.get("confident") is False:
            adds["unsure"] = 1  # a search the books could not answer; with no model call after it, the visitor was told "the books do not say"''')
rep('"errors", "refused", "llm_ms")', '"errors", "refused", "llm_ms", "unsure", "llm_calls")')
rep('''    into["refused"] += 1 if int(row.get("refused", 0) or 0) else 0''', '''    # a refusal: the app said so, or the books were not confident and no model call followed (an agent that searches again is not a refusal)
    into["refused"] += 1 if (int(row.get("refused", 0) or 0) or (int(row.get("unsure", 0) or 0) and not int(row.get("llm_calls", 0) or 0))) else 0''')
open(p, "w", encoding="utf8").write(s)

p = "tests/test_traces.py"
t = open(p, encoding="utf8").read()
t = t.rstrip("\n") + '''

# a search the books could not answer, with no model call, is a refusal even when the app reports nothing (older kits, the cached-answer route)
traces._store[:] = [traces.MemoryTraces()]
unsure_id, agent_id = tc.new_trace_id(), tc.new_trace_id()
item_now = portal.db().get(f"tok#{tid}")
traces.record(item_now, unsure_id, "retrieval", "retriever", {"confident": False, "hit_count": 0}, now=NOW)
traces.record(item_now, agent_id, "retrieval", "retriever", {"confident": False}, now=NOW)
traces.record(item_now, agent_id, "llm", "llm", {"model": "m"}, now=NOW, dur_ms=500)
today_numbers = traces.compute_day(traces.day_id(NOW))
assert today_numbers["answers"] == 2 and today_numbers["refused"] == 1, today_numbers
print("  ok  an unsure search with no model call counts as a refusal; an agent that searched again does not")
'''
open(p, "w", encoding="utf8").write(t)
print("ok")
