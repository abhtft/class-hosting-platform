"""Knowledge worker Lambda: reads one uploaded book. Started asynchronously by knowledge.start() with {"scope", "owner", "collection", "doc_id"}.

It has a long time limit and a big memory (see the template); everything it does, and every limit it enforces, is in knowledge.process_document(). It never
raises for a bad file: the failure is written into the document's status where the teacher or student reads it.
"""
import time

import knowledge


def handler(event, context=None, now=None):
    now = int(now if now is not None else time.time())
    status = knowledge.process_document(event["scope"], event["owner"], event["collection"], event["doc_id"], now)
    return {"state": (status or {}).get("state"), "doc_id": event.get("doc_id")}
