"""The platform function that runs one background job (see jobs.py).

The gateway starts it asynchronously with {"slug", "id"} and Lambda must not retry it (the template turns retries off): a retry would run
the student's job twice. It calls the student's job function, waits for it (up to 15 minutes), and records the outcome in the tokens table.
"""
import jobs


def handler(event, context=None):
    jobs.run(event["slug"], event["id"])
    return {"ok": True}
