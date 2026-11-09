"""Manual capacity check against a REAL platform (not part of the automatic suite; it spends a few cents of AI).

It sends the same cheap request in parallel, at 1, 6, 12 and 30 at once, and prints how many were answered and how many were refused as
busy (HTTP 429). Run it before and after raising the account's Lambda concurrency limit.

    python tests/live_load.py --url https://<site>/api/ping --header "Authorization: Bearer <student token>"

Use any backend route that is cheap (a route of the starter that answers without the AI is enough to test the gateway; a route that asks the
class AI tests the whole chain). The numbers to expect: with a limit of 10, about 3 at a time; with 1000, nearly everything answered.
"""
import argparse
import concurrent.futures as cf
import time
import urllib.error
import urllib.request


def one(url, headers):
    request = urllib.request.Request(url, headers=headers)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, time.time() - started
    except urllib.error.HTTPError as error:
        return error.code, time.time() - started
    except Exception:
        return 0, time.time() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--header", action="append", default=[], help="'Name: value', repeatable")
    parser.add_argument("--levels", default="1,6,12,30")
    args = parser.parse_args()
    headers = dict(h.split(": ", 1) for h in args.header)
    for level in [int(x) for x in args.levels.split(",")]:
        with cf.ThreadPoolExecutor(max_workers=level) as pool:
            results = list(pool.map(lambda _: one(args.url, headers), range(level)))
        ok = sum(1 for status, _ in results if 200 <= status < 300)
        busy = sum(1 for status, _ in results if status == 429)
        other = level - ok - busy
        slowest = max(seconds for _, seconds in results)
        print(f"{level:>3} at once: {ok} answered, {busy} busy (429), {other} other; slowest {slowest:.1f}s")
        time.sleep(3)


if __name__ == "__main__":
    main()
