# 03. S3, CloudFront and the edge (includes CORS on S3)

Goal: understand exactly how a request for `https://alice.apps.example.com/cart` becomes bytes from a private bucket, why nobody can bypass CloudFront to read the bucket, how caching and invalidation keep sites fresh, and why the bucket has a CORS configuration while the APIs do not.

---

## Part 1. S3 theory and what we built

### 1.1 One bucket, five prefixes

S3 has no real folders: a "folder" is just a **key prefix**. Your single bucket (`SitesBucket`) uses prefixes as security zones:

| Prefix | Content | Who writes | Who can **read** (through CloudFront) |
|---|---|---|---|
| `sites/<slug>/` | Published student sites | DeployFunction | CloudFront (students' distribution + tenants) |
| `portal/` | The teacher/student web pages | `portal-publish` (installer/admin) | CloudFront (portal distribution only) |
| `uploads/` | Temporary zips (site and backend) | Students, via presigned PUT | **Nobody** through CloudFront |
| `archive/<slug>/` | Sites taken offline (never deleted) | Sweeper / teacher | **Nobody** through CloudFront |
| `userdata/<slug>/` | Class storage files | StorageFunction (via presigned POST) | Only via short-lived presigned GET links |

The separation is enforced by the **bucket policy** (it grants CloudFront `GetObject` only on `sites/*` and `portal/*`), not by trust. Moving a site to `archive/` takes it offline *because CloudFront cannot read that prefix*.

### 1.2 Block Public Access (BPA)

Four independent switches, all `true` on this bucket (template.yaml:167):

| Setting | Effect |
|---|---|
| `BlockPublicAcls` | Reject requests that try to set a public ACL |
| `IgnorePublicAcls` | Ignore any public ACL that exists |
| `BlockPublicPolicy` | Reject a bucket policy that would make the bucket public |
| `RestrictPublicBuckets` | Even if a public policy existed, restrict access to AWS services and authorised users |

BPA is a **safety net above policies**: even a future mistaken bucket policy could not make this bucket public. The bucket is reachable only by (a) CloudFront via OAC and (b) IAM principals with permissions (your Lambdas, you).

### 1.3 Origin Access Control (OAC) and how CloudFront reads a private bucket

1. The distribution's origin is the bucket's **REST endpoint** (`<bucket>.s3.us-east-1.amazonaws.com`), not the *website* endpoint (a website endpoint only works with public buckets).
2. `SitesOAC` (template.yaml:301): `SigningBehavior: always`, `SigningProtocol: sigv4`. CloudFront **signs every request** to S3 with **Signature Version 4**, using the CloudFront service's identity.
3. The bucket policy statement allows `s3:GetObject` to `Principal: {Service: cloudfront.amazonaws.com}` **with** `AWS:SourceArn = <your distribution ARN>` (the confused-deputy guard, see `02`).
4. `S3OriginConfig: {OriginAccessIdentity: ''}` is required (empty) when using OAC. (OAI, "origin access identity", is the older mechanism: OAC is the current one; it supports all regions, SSE-KMS, and PUT/DELETE.)

Result: `https://bucket.s3.amazonaws.com/sites/alice/index.html` from the internet = **403**; the same object through CloudFront = **200**.

> Why a missing site returns **403**, not 404: the CloudFront principal has `GetObject` but not `ListBucket`. When a caller lacks `ListBucket`, S3 answers 403 for a nonexistent key (to avoid revealing which keys exist).

### 1.3b Encryption, ownership, versioning, lifecycle

| Setting | What it is here | Why |
|---|---|---|
| Default encryption | Not set in the template -> S3 applies **SSE-S3 (AES-256)** to every new object automatically (since 2023) | Encryption at rest with no cost or key management. **Verify** with `get-bucket-encryption`. |
| Object Ownership | Default **bucket owner enforced**: ACLs are disabled | The bucket policy and IAM are the only access controls: simpler to reason about |
| **Versioning** (enabled) | Every overwrite/delete keeps the old version | "The undo button": a mistakenly overwritten site file can be recovered |
| Lifecycle `ExpireUploadedZips` | Prefix `uploads/`: delete after 1 day (and noncurrent versions after 1 day) | Temporary zips do not accumulate |
| Lifecycle `ExpireOldVersions` | Noncurrent versions deleted after **30 days**; incomplete multipart uploads aborted after 2 days | Bounded storage cost, 30-day recovery window |
| Lifecycle `CleanUpDeleteMarkers` | Remove expired delete markers | Tidy listing |

Because the bucket is **versioned**, `destroy` must empty **all versions and delete markers** before the bucket can be deleted (this is what the installer's "Emptying the sites bucket (all versions)" step does).

### 1.4 Presigned URLs: how the browser talks to S3 without credentials

A **presigned URL** is a normal S3 URL with a **SigV4 signature in the query string**:

```
https://bucket.s3.us-east-1.amazonaws.com/uploads/alice.zip
  ?X-Amz-Algorithm=AWS4-HMAC-SHA256
  &X-Amz-Credential=<access key id>/<date>/us-east-1/s3/aws4_request
  &X-Amz-Date=...  &X-Amz-Expires=600  &X-Amz-SignedHeaders=host  &X-Amz-Signature=<hmac>
```

Rules to internalise:

- It is a **delegation of the signer's permissions** for a limited time and for **exactly one method + key**. The signer here is the Lambda's role session; the URL can do no more than that role could.
- **Expiry:** `ExpiresIn=600` seconds in this platform (`app.py:136`, `backends.py:165`). A URL signed with *temporary* role credentials also stops working when those credentials expire, whichever is first.
- **What is signed:** method, path, query, and the headers listed in `SignedHeaders`. Anything **not signed** can be changed by the client. **A presigned PUT cannot limit the object's size** (the size is not part of the signature unless `Content-Length` is signed).
- **Presigned POST** is different: it carries a **policy document** that can include `content-length-range` and `Content-Type` conditions, and S3 enforces them **before** accepting the bytes. Class storage uses presigned POST (`storage.py:416`) with `["content-length-range", 1, size]`.

> **Real design observation:** site and backend uploads use presigned **PUT**, so the 50 MB (site) / 5 MB (backend) limits are checked **after** the upload, in `publish` (`HeadObject` -> too big -> delete the zip). A student could briefly upload a larger object; the lifecycle rule deletes `uploads/` after a day. Moving these to presigned POST with `content-length-range` would enforce the limit *before* storage. (A hardening idea, not a bug: the cost exposure is small.)

### 1.5 CORS on the bucket (theory, then what we set)

**What CORS is.** Browsers enforce the **same-origin policy**: a page from origin A may not read responses from origin B unless B opts in. An **origin** = scheme + host + port. **CORS** (Cross-Origin Resource Sharing) is B's opt-in, expressed as response headers (`Access-Control-Allow-Origin`, `-Methods`, `-Headers`).

**Two crucial facts**

1. **CORS is enforced by the browser only.** `curl`, a server, or a Lambda ignores it entirely. CORS is **not an access control**; it never stops an attacker with a script. It only decides whether *a web page* may read a cross-origin response. Authorization still needs real credentials (here: the signature).
2. **Simple vs preflighted requests.** A request that is "not simple" (a `PUT`, or custom headers like `Authorization`) first triggers a **preflight**: the browser sends `OPTIONS` with `Origin`, `Access-Control-Request-Method`, `Access-Control-Request-Headers`. The server must answer with allow headers. If it does not, the browser never sends the real request.

**Why the bucket needs CORS and the APIs do not.**

- The student page lives at `https://deploy.<domain>`. It uploads the zip with a `PUT` **directly to** `https://<bucket>.s3.us-east-1.amazonaws.com/...`: a **different origin**. The browser preflights; S3 answers according to the bucket's CORS configuration. So the **bucket** needs CORS (template.yaml:173 to 187).
- The portal calls its API at **the same origin** (`https://deploy.<domain>/api/...`, because CloudFront routes `/api/*` on the same hostname). Same origin = no CORS. That is a deliberate design: **put API and UI behind one CloudFront hostname and CORS disappears.** The same holds for student sites calling `/api` (same host, different path). So none of the HTTP APIs have a CORS configuration (verify in `04`).

**The two CORS rules on the bucket**

| Rule | Methods | Allowed origins | Purpose |
|---|---|---|---|
| 1 | `PUT` | `https://deploy.<RootDomain>` (custom domain) or `*` (when no custom domain, because the portal's `*.cloudfront.net` origin is not known in advance) | Student page uploads a zip to a presigned URL |
| 2 | `POST`, `GET` | `*` | A **student's own page** uploads a file with a presigned POST and reads one with a presigned GET (class storage). Their site origin varies, so `*`; the **signature** authorises each request |

`AllowedHeaders: ['*']`, `MaxAge: 3600` (the browser may cache the preflight for an hour).

Is `*` safe? Yes **because the presigned signature, not the origin, is the credential**: a page on `evil.com` cannot do anything without a valid presigned URL, and with one it could do the same from `curl`. CORS `*` would be dangerous if the bucket relied on *cookies or ambient credentials*; it does not.

---

## Part 2. CloudFront theory and what we built

### 2.1 The model

CloudFront is a CDN: hundreds of **edge locations** cache content close to viewers, backed by **regional edge caches**, in front of your **origins**. A **distribution** has:

- **Aliases** (alternate domain names, the CNAMEs) and a **certificate** (ACM, must be in **us-east-1**).
- **Origins** (where to fetch from) and **behaviors** (a **path pattern** -> which origin + policies). The **default behavior** (`*`) is last; other patterns are evaluated in order of precedence (`/api/*` is checked before `*`).
- **Policies:** *cache policy* (which parts of the request form the **cache key**, and TTLs), *origin request policy* (what to **forward** to the origin that is *not* part of the cache key), *response headers policy* (headers added to responses).
- **Functions:** code at the edge (see 2.4).

### 2.2 Your two (plus one) distributions

| Distribution | Alias | Origins | Behaviors |
|---|---|---|---|
| `Distribution` (students) | `*.<RootDomain>` | `sites` = S3 (OAC), `backend` = BackendApi (only if backend on) | `/api/*` -> backend, no cache, all methods; default `*` -> S3, cache, GET/HEAD |
| `PortalDistribution` | `deploy.<RootDomain>` | `portal` = S3 path `/portal` (OAC), `api` = DeployApi | `/api/*` -> api; default -> portal pages; security headers policy |
| **Multi-tenant distribution** (created by the installer) | one per student domain, via **tenants** | `sites` origin path `/sites/{{student}}`, `backend` origin path `/b/{{student}}` | same two behaviors |

Both CloudFormation distributions use `PriceClass_200`, `HttpVersion: http2and3`, `ViewerCertificate: sni-only`, `MinimumProtocolVersion: TLSv1.2_2021`.

- **Price class 200**: serve from a subset of edge locations (North America, Europe, Asia, Middle East, Africa; excludes the most expensive regions, e.g. South America and Australia/NZ): cheaper, slightly slower for excluded regions. **Verify** the current list.
- **SNI-only** certificates are free (dedicated-IP certificates cost about $600/month and are only needed for ancient clients).
- **`TLSv1.2_2021`** is a *security policy*: minimum TLS 1.2 with a modern cipher suite.

### 2.3 The managed policies by ID (decode them)

Your template references managed policies by UUID. You should know what they are:

| ID | Name | What it does |
|---|---|---|
| `658327ea-f89d-4fab-a63d-7e88639e58f6` | **Managed-CachingOptimized** | Cache key = path only (**no query strings, no cookies, no headers**), gzip + brotli, default TTL 1 day, max 1 year. Honours the origin's `Cache-Control`. |
| `4135ea2d-6df8-44a3-9df3-4b5a84be39ad` | **Managed-CachingDisabled** | TTL 0: never cached (used for `/api/*`) |
| `b689b0a8-53d0-40ab-baf2-68738e2966ac` | **Managed-AllViewerExceptHostHeader** (origin request policy) | Forward all viewer headers (including `Authorization`), cookies, query strings, **except `Host`** |
| `67f7725c-6f97-4210-82d7-5512b31e9d03` | **Managed-SecurityHeadersPolicy** | Adds `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `X-XSS-Protection` (**verify** exact values in the console). Used on the **portal** only. **No CSP.** |

**Why exclude the `Host` header for API origins.** API Gateway routes by its own hostname (`xxxx.execute-api.us-east-1.amazonaws.com`). If CloudFront forwarded the viewer's `Host: alice.apps.example.com`, API Gateway would not recognise it and refuse. So the policy omits `Host`, and CloudFront sends the origin's own domain.

**CachingOptimized and query strings.** Because the cache key is the path only, `style.css?v=2` and `style.css?v=3` hit the **same** cached object. A student who "cache-busts" with a query string will not see an effect: caching is controlled by **invalidation on publish**, not by URLs. (Static asset files that Vite emits have hashed *file names*, which works.)

### 2.4 CloudFront Functions (what runs at the edge)

Three of yours: `RouterFunction`, `BackendRouteFunction`, `PortalFunction` (+ two in the multi-tenant setup).

- **Event types:** `viewer-request` (before the cache lookup) and `viewer-response`. (Lambda@Edge also offers origin-request/response, with network access, but is heavier, slower to deploy, and costs more.)
- **Runtime:** `cloudfront-js-2.0` (a restricted JavaScript). **No network, no filesystem**; execution budget is about **1 ms** of compute; small code size limit (**verify** the current limits in the docs). Cost is about **$0.10 per million invocations**.
- **Because viewer-request runs before the cache,** it runs on *every* request, hit or miss. Keep it tiny.

#### RouterFunction, line by line (template.yaml:311)

```js
var ROOT_DOMAIN = '${RootDomain}'.toLowerCase();       // injected by CloudFormation !Sub
var NAME = /^[a-z0-9][a-z0-9-]{0,29}$/;                 // a legal student name: no dots, no slashes

function handler(event) {
  var host = request.headers.host.value.toLowerCase();
  if (ROOT_DOMAIN && host.endsWith('.' + ROOT_DOMAIN)) {
    student = host.slice(0, host.length - ROOT_DOMAIN.length - 1);   // alice.apps.example.com -> alice
    rest = request.uri;
  } else {                                              // path mode: dxxxx.cloudfront.net/alice/cart
    ...redirect /alice to /alice/ ; student = first path segment ; rest = remainder
  }
  if (!NAME.test(student)) return notFound();           // reject anything that is not a clean name
  var last = rest.slice(rest.lastIndexOf('/') + 1);
  if (last.indexOf('.') === -1) rest = '/index.html';   // SPA fallback: a last segment with no dot is a client-side route
  request.uri = '/sites/' + student + rest;             // the S3 key
  return request;
}
```

Three things to understand:

1. **Multi-tenancy by Host header.** One distribution serves every student; the *hostname* selects the folder. That is why the wildcard alias `*.<RootDomain>` and wildcard certificate exist.
2. **The name check is a security control.** `student` is built from user-influenced input (the Host header). The regex forbids `/`, `.` and everything else that could climb out of `sites/` (path traversal).
3. **SPA fallback** rewrites any path whose last segment has no `.` to `/index.html`. Examples: `/cart` -> `index.html`; `/assets/app.abc.js` -> unchanged (has a dot); `/v1.2/about` -> `index.html` (last segment `about`). A route like `/report.pdf` that is *not* a file would 404.

#### BackendRouteFunction and PortalFunction

`BackendRouteFunction`: for `alice.<domain>/api/ask` -> `request.uri = '/b/alice/ask'`; sets **`x-viewer-ip`** to `event.viewer.ip`, *overwriting anything the client sent*. `PortalFunction`: clean URLs (`/teacher` -> `/teacher.html`) and the same `x-viewer-ip` rule for `/api/*`. **Why:** behind CloudFront the API only sees CloudFront's IP; the real visitor IP must be passed as a header so rate limits and login lockout work per visitor. The header is trusted **only** together with the secret `x-origin-secret` header CloudFront adds (see `04`).

### 2.5 Caching and freshness (the publishing model)

`app.py` writes each file with:

- HTML: `Cache-Control: public, max-age=0, must-revalidate, s-maxage=31536000`
- everything else: `public, max-age=300, s-maxage=31536000`

`s-maxage` is for **shared caches** (CloudFront): **one year**. `max-age` is for the **browser**: HTML always revalidates; other files cached 5 minutes. After publish the platform sends an **invalidation** for `/sites/<slug>/*` and `/<slug>/*` (the *rewritten* key and the viewer path), so the edge drops the old objects at once.

- Invalidations: the **first 1,000 paths per month are free** (one wildcard `/x/*` counts as one path). Each publish uses 2 paths, about 500 deploys a month free. **Verify** current pricing.
- Pattern: **immutable long TTL + invalidate on change** gives instant freshness *and* maximum cache hit ratio.

### 2.6 Headers, TLS and what is *not* there

- `ViewerProtocolPolicy: redirect-to-https` (sites/portal), `https-only` for `/api/*`.
- Portal: managed security headers. **Student sites: none** (no CSP, no HSTS from CloudFront). Student pages are arbitrary user content on **sibling subdomains** (see the cookie/origin note in `09`).
- No **WAF** (AWS WAF costs money per rule/request). CloudFront includes **AWS Shield Standard** (network/transport DDoS protection) at no extra cost.

---

## Part 3. Console steps (Mode B)

### 3.1 S3

1. **S3** -> your bucket -> **Permissions**:
   - **Block public access**: all four *On*.
   - **Bucket policy**: three statements; check each `Resource` ends in `sites/*` or `portal/*` and each has `AWS:SourceArn`.
   - **Object Ownership**: *Bucket owner enforced*.
   - **Cross-origin resource sharing (CORS)**: the two rules.
2. **Properties**: *Bucket Versioning* = Enabled; *Default encryption* = SSE-S3.
3. **Management** -> *Lifecycle rules*: the three rules.
4. **Objects**: five prefixes. Toggle **List versions** to see old versions and delete markers.

### 3.2 CloudFront

1. **CloudFront** -> **Distributions** -> `Student frontends (...)`:
   - **General**: alternate domain `*.<root>`, certificate, price class, HTTP versions, security policy `TLSv1.2_2021`.
   - **Origins**: `sites` shows *Origin access control* = the OAC; `backend` shows *Origin custom headers*: `x-origin-secret` (value hidden/visible depending on permissions).
   - **Behaviors**: `/api/*` above `Default (*)`. Open each: viewer protocol policy, allowed methods, cache policy name (CachingOptimized / CachingDisabled), origin request policy, **Function associations** (Viewer request).
2. **CloudFront** -> **Functions** -> `student-hosting-router` -> **Build** (the code), **Test** tab (see lab 4 below), **Publish**, **Associations**.
3. **CloudFront** -> **Policies** -> **Cache**, **Origin request**, **Response headers**: **Managed** tab: open the four IDs above and read them.
4. **CloudFront** -> **Security** -> **Origin access**: the OAC.
5. **CloudFront** -> **Monitoring**/**Reports**: *Cache statistics* (hit ratio), *Popular objects*.

---

## Part 4. CLI verification

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
DIST=$(aws cloudformation describe-stacks --stack-name $STACK --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)

# S3 posture  [READ]
aws s3api get-public-access-block --bucket $BUCKET                       # expect all four true
aws s3api get-bucket-policy --bucket $BUCKET --query Policy --output text | python -m json.tool
aws s3api get-bucket-cors --bucket $BUCKET
aws s3api get-bucket-versioning --bucket $BUCKET
aws s3api get-bucket-encryption --bucket $BUCKET                         # expect SSE-S3 (AES256)
aws s3api get-bucket-ownership-controls --bucket $BUCKET
aws s3api get-bucket-lifecycle-configuration --bucket $BUCKET

# CloudFront  [READ]
aws cloudfront get-distribution-config --id $DIST --query 'DistributionConfig.[Aliases.Items,PriceClass,HttpVersion,ViewerCertificate.MinimumProtocolVersion]'
aws cloudfront get-distribution-config --id $DIST --query 'DistributionConfig.Origins.Items[].[Id,DomainName,OriginAccessControlId]' --output table
aws cloudfront get-distribution-config --id $DIST --query 'DistributionConfig.CacheBehaviors.Items[].[PathPattern,CachePolicyId,OriginRequestPolicyId]' --output table
aws cloudfront list-functions --query 'FunctionList.Items[].[Name,FunctionMetadata.Stage]' --output table
aws cloudfront get-cache-policy --id 658327ea-f89d-4fab-a63d-7e88639e58f6 --query 'CachePolicy.CachePolicyConfig.[Name,MinTTL,DefaultTTL,MaxTTL]'
```

### 4.1 Experiments: prove the claims with `curl`

```bash
# 1) The bucket is private.                                  EXPECT 403
curl -s -o /dev/null -w "%{http_code}\n" "https://$BUCKET.s3.us-east-1.amazonaws.com/sites/alice/index.html"

# 2) The same file through CloudFront.                       EXPECT 200
curl -s -o /dev/null -w "%{http_code}\n" https://alice.<your-domain>/

# 3) The uploads/ and archive/ prefixes are unreachable via CloudFront.   EXPECT 403/404, never the object
curl -s -o /dev/null -w "%{http_code}\n" --path-as-is "https://alice.<your-domain>/../uploads/alice.zip"

# 4) Caching: two requests; the second should be a Hit
curl -sI https://alice.<your-domain>/assets/<some-file>.js | grep -iE "x-cache|^age|cache-control"
curl -sI https://alice.<your-domain>/assets/<some-file>.js | grep -iE "x-cache|^age"

# 5) Headers: the portal has security headers, a student site does not
curl -sI https://deploy.<your-domain>/ | grep -iE "strict-transport|x-frame|x-content-type|referrer-policy"
curl -sI https://alice.<your-domain>/ | grep -iE "strict-transport|x-frame|content-security-policy"   # expect nothing

# (A bad host label cannot be tested with curl: the Host header must match the certificate. Use the function tester in 4.2.)
```

### 4.2 Test the router function without any traffic `[READ]` (CloudFront Functions have a built-in tester)

Save as `event.json`:

```json
{ "version": "1.0", "context": { "eventType": "viewer-request" },
  "viewer": { "ip": "203.0.113.9" },
  "request": { "method": "GET", "uri": "/cart", "querystring": {}, "headers": { "host": { "value": "alice.apps.example.com" } }, "cookies": {} } }
```

```bash
ETAG=$(aws cloudfront describe-function --name ${STACK}-router --stage LIVE --query ETag --output text)
aws cloudfront test-function --name ${STACK}-router --if-match "$ETAG" --stage LIVE --event-object fileb://event.json \
  --query 'TestResult.[FunctionOutput,ComputeUtilization]'
```

**Expect:** `request.uri` = `/sites/alice/index.html` (the SPA fallback), a small `ComputeUtilization` percentage. Change the host to `Bad_Name.apps.example.com` -> a `404` response object. Change `uri` to `/assets/app.js` -> `/sites/alice/assets/app.js`. (Replace `apps.example.com` with *your* `RootDomain`: the function has it baked in.)

---

## Part 5. What you should now be able to explain

1. Why can't someone read the bucket directly, even if they know the bucket name and a key? (BPA + bucket policy only allows CloudFront, only your distribution, only two prefixes.)
2. What does `AWS:SourceArn` protect against? (A different customer's CloudFront reading your bucket: confused deputy.)
3. Why is the bucket's CORS needed but the API's is not? (Cross-origin PUT to S3 vs same-origin `/api` through one CloudFront hostname.)
4. Why does `style.css?v=2` not bust the cache? (Cache key = path only; freshness is via invalidation.)
5. How can one distribution serve thousands of students? (Host-header routing in a viewer-request function + wildcard alias/cert.)
6. What stops `Host: ../../x` style tricks? (The name regex in the router function and the fixed `/sites/` prefix.)
7. Why does a missing site return 403? (No `ListBucket` for CloudFront.)
8. What are two limits of presigned PUT, and what fixes them? (Cannot cap size before upload; fix with presigned POST + `content-length-range`. Expires with the signing credentials.)
