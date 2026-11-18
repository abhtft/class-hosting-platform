"""Check a signed sign-in token (an OpenID Connect ID token from Amazon Cognito) using only the standard library.

Why not a library: the platform's functions ship without third-party packages (except one vendored PDF reader), and a signature check
is a small, well-defined piece of work. It is kept strict on purpose:

  * only RS256 is accepted; "none", HS256 and anything else are refused before any key is looked at;
  * the key is found by its id ("kid") in the pool's published key list; an unknown key id is refused;
  * the issuer, the audience (the app client the token was made for), the token use ("id"), the expiry and the start time are all checked;
  * the signature is checked with the RSA public key and the exact padding of RFC 8017 (EMSA-PKCS1-v1_5, SHA-256), compared in constant time.

verify_id_token() returns the claims or raises TokenError with a short reason that is safe to show.
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.request

LEEWAY_SECONDS = 60  # allowed clock difference between Cognito and this function
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")  # DigestInfo for SHA-256 (RFC 8017, section 9.2)
MAX_TOKEN_CHARS = 8192
JWKS_CACHE_SECONDS = 3600
JWKS_TIMEOUT_SECONDS = 5

_jwks_cache = {}


class TokenError(Exception):
    pass


def _b64decode(part):
    if not isinstance(part, str) or not part or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in part):
        raise TokenError("The sign-in token is malformed.")
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _json(part):
    try:
        value = json.loads(_b64decode(part))
    except (ValueError, UnicodeDecodeError):
        raise TokenError("The sign-in token is malformed.")
    if not isinstance(value, dict):
        raise TokenError("The sign-in token is malformed.")
    return value


def rsa_verify(n, e, message, signature):
    """RSASSA-PKCS1-v1_5 with SHA-256. True only for an exact match."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k or k < 62 + 8:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    digest_info = SHA256_PREFIX + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return hmac.compare_digest(em, expected)


def _int(b64):
    return int.from_bytes(_b64decode(b64), "big")


def verify_id_token(token, issuer, audience, keys, now=None):
    """keys: the "keys" list of the pool's JWKS document. Returns the claims."""
    now = int(now if now is not None else time.time())
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise TokenError("The sign-in token is malformed.")
    head_b64, body_b64, sig_b64 = token.split(".")
    header = _json(head_b64)
    if header.get("alg") != "RS256":
        raise TokenError("The sign-in token uses an unsupported signature type.")
    key = next((k for k in keys or [] if isinstance(k, dict) and k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if not key or not header.get("kid"):
        raise TokenError("The sign-in token was not made by this platform's sign-in service.")
    try:
        n, e = _int(key["n"]), _int(key["e"])
    except (KeyError, TokenError):
        raise TokenError("The sign-in token was not made by this platform's sign-in service.")
    if not rsa_verify(n, e, (head_b64 + "." + body_b64).encode("ascii"), _b64decode(sig_b64)):
        raise TokenError("The sign-in token's signature is not valid.")
    claims = _json(body_b64)
    if claims.get("iss") != issuer:
        raise TokenError("The sign-in token comes from another sign-in service.")
    if claims.get("token_use") != "id":
        raise TokenError("This is not an ID token.")
    aud = claims.get("aud")
    if aud != audience:  # a list is never accepted: the token must be made for exactly this app client
        raise TokenError("The sign-in token was made for another application.")
    exp, iat = claims.get("exp"), claims.get("iat")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= now - LEEWAY_SECONDS:
        raise TokenError("The sign-in token has expired. Sign in again.")
    if isinstance(iat, bool) or not isinstance(iat, (int, float)) or iat > now + LEEWAY_SECONDS:
        raise TokenError("The sign-in token is not valid yet.")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenError("The sign-in token has no account id.")
    return claims


def fetch_jwks(issuer, now=None, fetch=None):
    """The pool's public keys, cached for an hour. `fetch(url)` returns the document text (tests pass their own)."""
    now = int(now if now is not None else time.time())
    cached = _jwks_cache.get(issuer)
    if cached and 0 <= now - cached[0] < JWKS_CACHE_SECONDS:
        return cached[1]
    url = issuer.rstrip("/") + "/.well-known/jwks.json"
    if not url.startswith("https://"):
        raise TokenError("The sign-in service address is not valid.")
    try:
        text = fetch(url) if fetch else urllib.request.urlopen(url, timeout=JWKS_TIMEOUT_SECONDS).read().decode()  # noqa: S310 (https only, checked above)
        keys = json.loads(text)["keys"]
    except Exception:
        raise TokenError("The sign-in service could not be reached. Try again in a moment.")
    if not isinstance(keys, list):
        raise TokenError("The sign-in service answered with something unexpected.")
    _jwks_cache[issuer] = (now, keys)
    return keys


def forget_keys():
    _jwks_cache.clear()
