"""Unit tests for the zip validation / content-type / warning logic in lambda/app.py. Needs: pip install boto3"""
import io
import os
import sys
import zipfile

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b",
                  DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
import app  # noqa: E402


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return zipfile.ZipFile(buf)


def entries(files):
    return sorted(app.site_entries(make_zip(files)))


def rejects(files, fragment):
    try:
        app.site_entries(make_zip(files))
    except app.DeployError as e:
        assert fragment in str(e), f"expected '{fragment}' in '{e}'"
        return
    raise AssertionError(f"expected DeployError containing '{fragment}'")


assert entries({"index.html": "x", "assets/a.js": "y"}) == ["assets/a.js", "index.html"]
assert entries({"dist/index.html": "x", "dist/assets/a.js": "y"}) == ["assets/a.js", "index.html"], "zipped folder itself"
assert entries({"index.html": "x", "__MACOSX/._index.html": "", ".DS_Store": ""}) == ["index.html"], "junk ignored"
rejects({"index.html": "x", "../evil.txt": "y"}, "Unsafe path")
rejects({"index.html": "x", "/etc/passwd": "y"}, "Unsafe path")
rejects({"a/one.js": "x", "b/two.js": "y"}, "index.html not found")

assert app.content_type("a.js") == "text/javascript; charset=utf-8"
assert app.content_type("a.css") == "text/css; charset=utf-8"
assert app.content_type("a.svg") == "image/svg+xml"
assert app.content_type("a.woff2") == "font/woff2"
assert app.content_type("a.unknown") == "application/octet-stream"
assert "max-age=0" in app.cache_control("index.html") and "max-age=300" in app.cache_control("assets/a.js")

# path mode (ROOT_DOMAIN empty): root-absolute asset URLs break under /<name>/
assert app.base_path_warning("alice", b'<script src="/assets/x.js">') is not None
assert app.base_path_warning("alice", b'<script src="/alice/assets/x.js">') is None
assert app.base_path_warning("alice", b'<script src="./assets/x.js"><a href="//cdn.example.com">') is None
app.ROOT_DOMAIN = "example.com"
assert app.base_path_warning("alice", b'<script src="/assets/x.js">') is None, "no warning in subdomain mode"
assert app.site_url("alice") == "https://alice.example.com/"

print("test_lambda.py: all passed")
