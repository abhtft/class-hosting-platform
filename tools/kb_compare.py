#!/usr/bin/env python3
"""Compare the current book search with an Amazon Bedrock Knowledge Base on the same questions (track B, step B0 of docs/notes/EXTRAS-RETRIEVAL-PLAN.md).

It runs on YOUR computer with YOUR AWS credentials and changes nothing that customers use: it copies the books of one business to the Knowledge Base's source prefix
(kb-sources/<business>/<shelf>/, with a metadata file for each book), starts an ingestion job and waits for it, then asks the same questions of both searches and prints a table.

  python tools/kb_compare.py --tenant poc --folder knowledge/poc --collections faq,prices --cases cases.json \\
      --bucket <sites bucket> --secret-name <AiSecretName> --knowledge-base <BooksKnowledgeBaseId> --data-source <BooksDataSourceId>

The cases file is the one `chatwoot_ctl.py eval` reads: a JSON list of {"question", "expect"?: "text that must be in a returned passage", "refuse"?: true}. Add --skip-upload to ask
questions only (the sources are already in), --k for the number of passages, --kb-min-score for the score at which the Knowledge Base counts as "sure" (the current search has its own rule).
Needs the stack to have BedrockKbEnabled=true and Bedrock model access for Amazon Titan Text Embeddings V2.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))

SUPPORTED = (".pdf", ".txt", ".md", ".html", ".htm", ".docx")


def log(what, **details):
    """One structured line: names, counts and codes only (never a book's text or a key)."""
    print(json.dumps({"kb_compare": what, **details}))


def source_files(folder, collections):
    """[(shelf, file name, path)] of the books to copy: one sub-folder per shelf, supported types only."""
    found = []
    for shelf in collections:
        base = os.path.join(folder, shelf)
        if not os.path.isdir(base):
            raise SystemExit(f"The shelf folder {base} does not exist.")
        for name in sorted(os.listdir(base)):
            if name.lower().endswith(SUPPORTED) and os.path.isfile(os.path.join(base, name)):
                found.append((shelf, name, os.path.join(base, name)))
    if not found:
        raise SystemExit("No books found in the shelf folders.")
    return found


def upload_sources(s3, bucket, tenant, files):
    """Copy each book and its metadata file (so a search can be limited to a business and a shelf). Returns the number of books."""
    for shelf, name, path in files:
        key = f"kb-sources/{tenant}/{shelf}/{name}"
        with open(path, "rb") as handle:
            s3.put_object(Bucket=bucket, Key=key, Body=handle.read(), ServerSideEncryption="AES256")
        s3.put_object(Bucket=bucket, Key=key + ".metadata.json", Body=json.dumps({"metadataAttributes": {"tenant": tenant, "collection": shelf}}).encode("utf8"),
                      ServerSideEncryption="AES256", ContentType="application/json")
        log("uploaded", shelf=shelf, book=name)
    return len(files)


def ingest(agent, knowledge_base, data_source, timeout=900, pause=10, clock=time.monotonic, sleep=time.sleep):
    """Start an ingestion job and wait. Returns the job's statistics; raises SystemExit when it fails or takes too long."""
    started = agent.start_ingestion_job(knowledgeBaseId=knowledge_base, dataSourceId=data_source)["ingestionJob"]["ingestionJobId"]
    limit = clock() + timeout
    while True:
        job = agent.get_ingestion_job(knowledgeBaseId=knowledge_base, dataSourceId=data_source, ingestionJobId=started)["ingestionJob"]
        if job["status"] == "COMPLETE":
            log("ingested", statistics=job.get("statistics", {}))
            return job.get("statistics", {})
        if job["status"] == "FAILED":
            raise SystemExit("The ingestion job failed: " + "; ".join(job.get("failureReasons", []))[:300])
        if clock() > limit:
            raise SystemExit("The ingestion job did not finish in time; check it in the Bedrock console and run again with --skip-upload.")
        sleep(pause)


def kb_search(runtime, knowledge_base, question, tenant, collections, k):
    """The Knowledge Base's best passages for a question, limited to the business and its shelves: [{"text", "score", "source"}] best first."""
    answer = runtime.retrieve(knowledgeBaseId=knowledge_base, retrievalQuery={"text": question}, retrievalConfiguration={"vectorSearchConfiguration": {
        "numberOfResults": k, "filter": {"andAll": [{"equals": {"key": "tenant", "value": tenant}}, {"in": {"key": "collection", "value": list(collections)}}]}}})
    hits = [{"text": (r.get("content") or {}).get("text", ""), "score": float(r.get("score") or 0), "source": ((r.get("location") or {}).get("s3Location") or {}).get("uri", "")} for r in answer.get("retrievalResults", [])]
    return sorted(hits, key=lambda h: -h["score"])


def judge(case, texts, sure):
    """True when the search did what the case wants: a question to refuse must not be 'sure'; an answerable one must be sure and (when `expect` is given) show it in a passage."""
    if case.get("refuse"):
        return not sure
    expect = str(case.get("expect") or "").lower()
    return bool(sure and (not expect or any(expect in text.lower() for text in texts)))


def compare(cases, own_search, kb_ask, kb_min_score, clock=time.monotonic):
    """One row per case: how each search did, its best score and its time. own_search(question) -> {"hits": [{"text", "title", "vector"}], "confident"}; kb_ask(question) -> [{"text", "score"}]."""
    rows = []
    for case in cases:
        started = clock()
        own = own_search(case["question"])
        own_ms = int((clock() - started) * 1000)
        started = clock()
        hits = kb_ask(case["question"])
        kb_ms = int((clock() - started) * 1000)
        kb_sure = bool(hits) and hits[0]["score"] >= kb_min_score
        rows.append({"question": case["question"], "refuse": bool(case.get("refuse")),
                     "own_ok": judge(case, [h["text"] + " " + h.get("title", "") for h in own["hits"]], own["confident"]), "own_best": round(own["hits"][0]["vector"], 2) if own["hits"] else None, "own_ms": own_ms,
                     "kb_ok": judge(case, [h["text"] for h in hits], kb_sure), "kb_best": round(hits[0]["score"], 2) if hits else None, "kb_ms": kb_ms})
    return rows


def summary(rows):
    """Counts and mean times for the two searches, answerable and refused questions apart."""
    out = {}
    for who in ("own", "kb"):
        answer = [r for r in rows if not r["refuse"]]
        refuse = [r for r in rows if r["refuse"]]
        out[who] = {"answerable": f"{sum(r[who + '_ok'] for r in answer)}/{len(answer)}", "refused": f"{sum(r[who + '_ok'] for r in refuse)}/{len(refuse)}",
                    "mean_ms": int(sum(r[who + "_ms"] for r in rows) / len(rows)) if rows else 0}
    return out


def parser():
    p = argparse.ArgumentParser(prog="kb_compare.py", description="Compare the current book search with a Bedrock Knowledge Base on the same questions.")
    p.add_argument("--tenant", required=True)
    p.add_argument("--folder", help="the business's books: one sub-folder per shelf (for example knowledge/poc)")
    p.add_argument("--collections", required=True, help="comma-separated shelf names")
    p.add_argument("--cases", required=True, help="the JSON file `chatwoot_ctl.py eval` reads")
    p.add_argument("--bucket", default=os.environ.get("BUCKET"), help="the sites bucket (or BUCKET)")
    p.add_argument("--secret-name", default=None, help="the AI secret's name (the stack's AiSecretName), for the current search")
    p.add_argument("--knowledge-base", required=True, help="the stack output BooksKnowledgeBaseId")
    p.add_argument("--data-source", required=True, help="the stack output BooksDataSourceId")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--kb-min-score", type=float, default=0.5)
    p.add_argument("--skip-upload", action="store_true", help="do not copy the books or ingest: only ask the questions")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    return p


def main(argv=None, s3=None, agent=None, runtime=None, own_search=None, out=print):
    args = parser().parse_args(argv)
    collections = [part.strip() for part in args.collections.split(",") if part.strip()]
    try:
        with open(args.cases, encoding="utf8") as handle:
            cases = json.load(handle)
    except (OSError, ValueError) as error:
        out(f"The cases file cannot be read: {error}")
        return 2
    if not isinstance(cases, list) or not all(isinstance(c, dict) and isinstance(c.get("question"), str) for c in cases):
        out('The cases file is a JSON list of {"question": "...", "expect": "...", "refuse": true} objects.')
        return 2
    if s3 is None or agent is None or runtime is None:
        import boto3
        s3 = s3 or boto3.client("s3", region_name=args.region)
        agent = agent or boto3.client("bedrock-agent", region_name=args.region)
        runtime = runtime or boto3.client("bedrock-agent-runtime", region_name=args.region)
    if not args.skip_upload:
        if not args.folder or not args.bucket:
            out("Give --folder (the books) and --bucket (the sites bucket), or use --skip-upload.")
            return 2
        upload_sources(s3, args.bucket, args.tenant, source_files(args.folder, collections))
        ingest(agent, args.knowledge_base, args.data_source)
    if own_search is None:
        import chatwoot_ctl
        import embeddings as em
        import chatwoot_runtime as engine
        shelves = chatwoot_ctl.real_shelves(args)
        if shelves is None:
            out("Give the sites bucket with --bucket (or BUCKET) and the AI secret's name with --secret-name for the current search.")
            return 2
        cache = em.MemoryCache()
        own_search = lambda question: engine.search_shelves(shelves, args.tenant, collections, question, k=args.k, cache=cache)  # noqa: E731
    rows = compare(cases, own_search, lambda question: kb_search(runtime, args.knowledge_base, question, args.tenant, collections, args.k), args.kb_min_score)
    for row in rows:
        out(f"{'refuse' if row['refuse'] else 'answer':6}  own={'PASS' if row['own_ok'] else 'FAIL'} ({row['own_best']}, {row['own_ms']} ms)  kb={'PASS' if row['kb_ok'] else 'FAIL'} ({row['kb_best']}, {row['kb_ms']} ms)  | {row['question'][:70]}")
    totals = summary(rows)
    out(f"current search: answerable {totals['own']['answerable']}, refused {totals['own']['refused']}, mean {totals['own']['mean_ms']} ms")
    out(f"knowledge base: answerable {totals['kb']['answerable']}, refused {totals['kb']['refused']}, mean {totals['kb']['mean_ms']} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
