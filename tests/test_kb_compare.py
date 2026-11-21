"""tools/kb_compare.py compares the current book search with a Bedrock Knowledge Base on the same questions: the books and their metadata files are copied to the source prefix,
an ingestion job is waited for (a failure or a timeout stops with a clear message), the Knowledge Base is asked with a filter on the business and its shelves, and each case is judged
the same way for both searches. Clients are fakes: no network."""
import json
import os
import sys
import tempfile

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "tools"))
sys.path.insert(0, os.path.join(root, "lambda"))
import kb_compare as kb  # noqa: E402

kb.log = lambda what, **details: None


class S3:
    def __init__(self):
        self.puts = {}

    def put_object(self, **args):
        self.puts[args["Key"]] = args


class Agent:
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), []

    def start_ingestion_job(self, **args):
        self.calls.append(("start", args))
        return {"ingestionJob": {"ingestionJobId": "job1"}}

    def get_ingestion_job(self, **args):
        self.calls.append(("get", args))
        status = self.statuses.pop(0)
        return {"ingestionJob": {"status": status, "statistics": {"numberOfNewDocumentsIndexed": 2}, "failureReasons": ["bad file"] if status == "FAILED" else []}}


class Runtime:
    def __init__(self, results):
        self.results, self.calls = results, []

    def retrieve(self, **args):
        self.calls.append(args)
        return {"retrievalResults": self.results(args["retrievalQuery"]["text"])}


# ---- the books and their metadata
folder = tempfile.mkdtemp()
os.makedirs(os.path.join(folder, "faq"))
open(os.path.join(folder, "faq", "hours.txt"), "w").write("Open nine to six.")
open(os.path.join(folder, "faq", "notes.exe"), "w").write("not a book")
files = kb.source_files(folder, ["faq"])
assert [(s, n) for s, n, _ in files] == [("faq", "hours.txt")], "only supported types"
s3 = S3()
assert kb.upload_sources(s3, "bucket", "poc", files) == 1
assert set(s3.puts) == {"kb-sources/poc/faq/hours.txt", "kb-sources/poc/faq/hours.txt.metadata.json"} and s3.puts["kb-sources/poc/faq/hours.txt"]["Body"] == b"Open nine to six."
assert json.loads(s3.puts["kb-sources/poc/faq/hours.txt.metadata.json"]["Body"]) == {"metadataAttributes": {"tenant": "poc", "collection": "faq"}}, "a search can be limited to a business and a shelf"
for bad in (["missing"],):
    try:
        kb.source_files(folder, bad)
        raise AssertionError(bad)
    except SystemExit:
        pass
print("  ok  the books and a metadata file each are copied to kb-sources/<business>/<shelf>/ (supported types only)")

# ---- the ingestion job
agent = Agent(["STARTING", "IN_PROGRESS", "COMPLETE"])
assert kb.ingest(agent, "KB", "DS", timeout=100, pause=0, sleep=lambda s: None) == {"numberOfNewDocumentsIndexed": 2} and agent.calls[0] == ("start", {"knowledgeBaseId": "KB", "dataSourceId": "DS"})
for statuses, word in ((["FAILED"], "bad file"),):
    try:
        kb.ingest(Agent(statuses), "KB", "DS", pause=0, sleep=lambda s: None)
        raise AssertionError("a failed job must stop")
    except SystemExit as stop:
        assert word in str(stop)
ticks = iter(range(0, 10000, 400))
try:
    kb.ingest(Agent(["IN_PROGRESS"] * 50), "KB", "DS", timeout=900, pause=0, clock=lambda: next(ticks), sleep=lambda s: None)
    raise AssertionError("a job that never ends must time out")
except SystemExit as stop:
    assert "did not finish in time" in str(stop)
print("  ok  ingestion waits for the job, and stops with the reason on a failure or a timeout")

# ---- asking the Knowledge Base
runtime = Runtime(lambda q: [{"content": {"text": "Open nine to six."}, "score": 0.71, "location": {"s3Location": {"uri": "s3://b/kb-sources/poc/faq/hours.txt"}}}, {"content": {"text": "Closed Sunday."}, "score": 0.83}])
hits = kb.kb_search(runtime, "KB", "when do you open", "poc", ["faq", "prices"], 5)
assert [h["score"] for h in hits] == [0.83, 0.71] and hits[1]["source"].endswith("hours.txt")
config = runtime.calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]
assert config["numberOfResults"] == 5 and config["filter"] == {"andAll": [{"equals": {"key": "tenant", "value": "poc"}}, {"in": {"key": "collection", "value": ["faq", "prices"]}}]}, "limited to the business and its shelves"
assert kb.kb_search(Runtime(lambda q: []), "KB", "x", "poc", ["faq"], 3) == []
print("  ok  the Knowledge Base is asked with a filter on the business and its shelves, best passage first")

# ---- the comparison
assert kb.judge({"refuse": True}, [], False) and not kb.judge({"refuse": True}, ["x"], True)
assert kb.judge({"expect": "nine"}, ["Open Nine to six"], True) and not kb.judge({"expect": "nine"}, ["closed"], True) and not kb.judge({"expect": "nine"}, ["nine"], False) and kb.judge({}, [], True)
cases = [{"question": "hours?", "expect": "nine"}, {"question": "capital of france?", "refuse": True}, {"question": "price?", "expect": "500"}]
own = {"hours?": {"hits": [{"text": "Open nine to six.", "title": "Faq", "vector": 0.8}], "confident": True}, "capital of france?": {"hits": [], "confident": False},
       "price?": {"hits": [{"text": "unrelated", "title": "Faq", "vector": 0.4}], "confident": False}}
kbr = {"hours?": [{"text": "We open at nine.", "score": 0.9}], "capital of france?": [{"text": "Open nine to six.", "score": 0.55}], "price?": [{"text": "A shirt costs 500 rupees.", "score": 0.7}]}
rows = kb.compare(cases, lambda q: own[q], lambda q: kbr[q], 0.6, clock=iter(range(0, 1000, 5)).__next__)
assert [(r["own_ok"], r["kb_ok"]) for r in rows] == [(True, True), (True, True), (False, True)] and rows[0]["own_best"] == 0.8 and rows[1]["own_best"] is None and rows[1]["kb_best"] == 0.55
totals = kb.summary(rows)
assert totals["own"]["answerable"] == "1/2" and totals["kb"]["answerable"] == "2/2" and totals["own"]["refused"] == "1/1" and totals["kb"]["refused"] == "1/1" and totals["kb"]["mean_ms"] > 0
print("  ok  each case is judged the same way for both searches (refusals, expected text, the Knowledge Base's own 'sure' score) and the totals add up")

# ---- the command
cases_path = os.path.join(folder, "cases.json")
json.dump(cases, open(cases_path, "w"))
lines = []
code = kb.main(["--tenant", "poc", "--folder", folder, "--collections", "faq", "--cases", cases_path, "--bucket", "bucket", "--knowledge-base", "KB", "--data-source", "DS", "--kb-min-score", "0.6"],
               s3=S3(), agent=Agent(["COMPLETE"]), runtime=Runtime(lambda q: [{"content": {"text": h["text"]}, "score": h["score"]} for h in kbr[q]]), own_search=lambda q: own[q], out=lines.append)
assert code == 0 and any("knowledge base: answerable 2/2" in line for line in lines) and any("current search: answerable 1/2" in line for line in lines)
lines.clear()
assert kb.main(["--tenant", "poc", "--collections", "faq", "--cases", cases_path, "--knowledge-base", "KB", "--data-source", "DS"], s3=S3(), agent=Agent([]), runtime=Runtime(lambda q: []), own_search=lambda q: own[q], out=lines.append) == 2
assert kb.main(["--tenant", "poc", "--collections", "faq", "--cases", os.path.join(folder, "none.json"), "--knowledge-base", "KB", "--data-source", "DS", "--skip-upload"], s3=S3(), agent=Agent([]), runtime=Runtime(lambda q: []), own_search=lambda q: own[q], out=lines.append) == 2
print("  ok  the command copies, ingests, asks both searches and prints the table; missing arguments and a bad cases file are refused")

print("test_kb_compare.py: the Bedrock Knowledge Base comparison tool")
