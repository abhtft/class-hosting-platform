"""The Python helper (student/class_storage.py) against the real storage API: data and files, every error kind, the local folder on a
student's computer, and that no key ever shows up in an error."""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "student"))
import class_storage as storage  # noqa: E402

fixture = subprocess.Popen([sys.executable, os.path.join(here, "storage_fixture.py")], stdout=subprocess.PIPE, text=True)
try:
    info = json.loads(fixture.stdout.readline())

    def use(key, url=None):
        os.environ["CLASS_STORAGE_URL"], os.environ["CLASS_STORAGE_KEY"] = url or info["url"], key

    def fails(kind, fn, *args, **kw):
        try:
            fn(*args, **kw)
        except storage.StorageError as e:
            assert e.kind == kind, (kind, e.kind, str(e))
            assert info["key"] not in str(e) and info["tight_key"] not in str(e), "a key must never be in an error"
            return e
        raise AssertionError(f"expected StorageError({kind})")

    # ---------- on the platform ----------
    use(info["key"])
    assert storage.put("scores", "alice", {"points": 3, "name": "Zoë"})["id"] == "alice"
    assert storage.get("scores", "alice") == {"points": 3, "name": "Zoë"}
    assert storage.get("scores", "nobody") is None and storage.get("scores", "nobody", default="d") == "d"
    for i in range(3):
        storage.put("scores", f"p{i}", i)
    assert [r["id"] for r in storage.items("scores")] == ["alice", "p0", "p1", "p2"]
    assert [r["id"] for r in storage.items("scores", limit=2)] == ["alice", "p0"]
    assert [r["id"] for r in storage.items("scores", after="p0")] == ["p1", "p2"]
    assert storage.delete("scores", "alice") is True and storage.delete("scores", "alice") is False
    print("  ok  put/get/default/items/paging/delete through the real API")

    fails("bad_request", storage.put, "Bad Name", "x", 1)
    fails("bad_request", storage.get, "scores", "../x")
    fails("too_large", storage.put, "scores", "big", "x" * 70000)
    print("  ok  names are checked and a value over 64 KB is refused with too_large")

    storage.files.put("note.txt", "hello file", "text/plain")
    assert [(f["name"], f["size"]) for f in storage.files.list()] == [("note.txt", 10)]
    link = storage.files.url("note.txt")
    assert urllib.request.urlopen(link).read() == b"hello file"
    target = storage.files.upload_url("pic.png", 100, "image/png")
    assert target["url"].endswith("/s3-post") and target["fields"]["Content-Type"] == "image/png" and target["fields"]["key"].endswith("/pic.png")
    fails("too_large", storage.files.put, "huge.bin", b"x" * (5 * 1024 * 1024 + 1))
    fails("bad_request", storage.files.put, "a/b.txt", b"x")
    assert storage.files.delete("note.txt") is True and storage.files.list() == []
    fails("not_found", storage.files.url, "note.txt")
    print("  ok  files: put (multipart, file last), list, download link, upload target, delete, limits")

    use(info["tight_key"])
    storage.put("t", "a", 1)
    storage.get("t", "a")
    fails("allowance_used", storage.get, "t", "a")
    print("  ok  allowance_used when the allowance is gone")

    use(info["off_key"])
    fails("not_enabled", storage.put, "t", "a", 1)
    use(info["key"].split(".")[0] + "." + "A" * 32)  # right student, wrong secret
    fails("bad_key", storage.get, "t", "a")
    use(info["key"], url="http://127.0.0.1:9/storage")  # nothing listens there
    fails("unavailable", storage.get, "t", "a")
    print("  ok  not_enabled, bad_key and unavailable (after one retry) are reported with a kind")

    # ---------- on the platform, storage not switched on: no pretending with local files ----------
    os.environ.pop("CLASS_STORAGE_URL"), os.environ.pop("CLASS_STORAGE_KEY")
    os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "sd-test"
    fails("not_enabled", storage.put, "t", "a", 1)
    del os.environ["AWS_LAMBDA_FUNCTION_NAME"]

    # ---------- on the student's own computer: a local folder ----------
    with tempfile.TemporaryDirectory() as folder:
        os.environ["CLASS_STORAGE_DIR"] = os.path.join(folder, ".class-storage")
        storage.put("notes", "n1", {"text": "local"})
        assert storage.get("notes", "n1") == {"text": "local"} and storage.get("notes", "n2", default=5) == 5
        storage.put("notes", "n2", [1, 2])
        assert [r["id"] for r in storage.items("notes")] == ["n1", "n2"] and [r["id"] for r in storage.items("notes", after="n1")] == ["n2"]
        assert storage.delete("notes", "n1") is True and storage.delete("notes", "n1") is False
        storage.files.put("a.txt", "x")
        assert storage.files.list() == [{"name": "a.txt", "size": 1}] and storage.files.url("a.txt").startswith("file:///")
        assert storage.files.upload_url("a.txt", 1)["local"] is True
        storage.files.delete("a.txt")
        assert storage.files.list() == []
        fails("bad_request", storage.put, "Bad", "x", 1)
        fails("too_large", storage.put, "notes", "big", "x" * 70000)
        assert os.path.isdir(os.environ["CLASS_STORAGE_DIR"])
    print("  ok  on a student's computer everything works from a local folder, with the same rules")
    print("test_class_storage.py: all passed")
finally:
    fixture.kill()
