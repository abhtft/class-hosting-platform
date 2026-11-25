# Chapter 3: The edge and storage: CloudFront, S3, DNS, certificates and custom domains

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.

## A. CloudFront

**Q71 [B] What is a CDN and what does CloudFront add to a plain S3 website?**
A CDN keeps copies of files near visitors. CloudFront adds caching, HTTPS with your own certificate, wildcard host routing, edge functions, and (here) a single front door for pages *and* APIs. A private S3 bucket cannot serve a website directly, so CloudFront is also the access path.
Study: [What is Amazon CloudFront?](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html)

**Q72 [B] What is a "distribution", an "origin" and a "behavior"?**
Distribution = the whole CDN configuration with its own `*.cloudfront.net` name. Origin = where CloudFront fetches from (the S3 bucket, an API Gateway). Behavior = a rule "for paths matching X, use origin Y with cache policy Z and these functions". Here the default behaviour is S3; `/api/*` is a second behaviour to the API.
Repo: `template.yaml` `Distribution`, `PortalDistribution`.

**Q73 [I] How does a single distribution serve every student at `<name>.<root-domain>`?**
DNS has a wildcard record `*.<root>` pointing at the distribution; the certificate covers `*.<root>`; and a CloudFront Function turns the Host header into the S3 key prefix `sites/<name>/`. No per-student CloudFront config needed (until a student brings their own domain).
Repo: `template.yaml` `RouterFunction`.

**Q74 [I] What is a CloudFront Function and how is it different from Lambda@Edge?**
CF Functions: tiny JavaScript, run at every edge location in under a millisecond, only manipulate the request/response, no network, no AWS SDK, cheap. Lambda@Edge: full Lambda at regional edge caches, can call the network, slower and costlier. Routing and header injection only need the former.
Study: [Choosing between CloudFront Functions and Lambda@Edge](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/edge-functions-choosing.html)

**Q75 [I] Why does the router function validate the name with a regex before using it?**
The host header is user-controlled. Without validation `../` or odd characters could produce an unexpected S3 key. The regex `^[a-z0-9][a-z0-9-]{0,29}$` ensures only legal slugs are mapped; anything else returns 404.

**Q76 [I] What is the SPA fallback and why is it in the router?**
Single-page apps use client-side routes (`/cart`) that have no file on S3. If the last path segment has no `.`, the function rewrites to `/index.html` so the browser loads the app and the JavaScript router takes over. Real files (`app.js`) have extensions and are fetched normally.

**Q77 [I] What is Origin Access Control (OAC) and how does the bucket stay private?**
OAC makes CloudFront sign its S3 requests (SigV4). The bucket policy allows `s3:GetObject` only to `cloudfront.amazonaws.com` with `AWS:SourceArn` equal to specific distributions and only for certain prefixes (`sites/*`, `portal/*`). Public access block is on; no one else can read.
Study: [Restricting access to an S3 origin](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)

**Q78 [I] Why are `uploads/` and `userdata/` not reachable through the CDN?**
Bucket policy statements are per prefix. Only `sites/*` and `portal/*` are readable by CloudFront; `uploads/` (temporary zips), `userdata/` (student files), `knowledge/`, `projects/`, `archive/` have no CDN access, only Lambda IAM access or short-lived presigned URLs.

**Q79 [I] Why separate caching for pages vs `/api/*`?**
Pages are identical for everyone: cache at the edge (`CachingOptimized`). API answers are per-visitor and often authenticated: caching disabled, and the `Authorization` header forwarded.
Study: [Cache policies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/controlling-the-cache-key.html)

**Q80 [B] Why must a CloudFront certificate be in `us-east-1`?**
CloudFront is a global service that reads its ACM certificates from `us-east-1` only. That is also why the platform stack lives in `us-east-1`.

**Q81 [I] What does the response headers policy do in this template?**
`StudentSitesHeadersPolicy` adds `nosniff` (forced), HSTS and a `strict-origin-when-cross-origin` referrer policy (both overridable by the student's own headers), and deliberately sends **no** frame restriction so a school page can embed a student site. The portal uses AWS's managed security-headers policy instead. Setting headers at the CDN means every student page gets them without trusting student code.
Study: [MDN: Content-Security-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP)

**Q82 [D] What does CloudFront "verification" mean and why did it block installs on a new account?**
New AWS accounts must be verified by AWS before CloudFront resources can be created; the error appears at stack-create time (not at `list-distributions`). The installer reads stack events and turns it into a support-case instruction. `docs/TEACHER_ARCHITECTURE.md` section 3.2a explains how.

**Q83 [D] Why create the sample "invalidation" per publish instead of versioned filenames?**
Student sites use whatever build tool they like; hashed asset names (Vite) already avoid stale assets, but `index.html` itself is not hashed. Invalidating the student's prefix keeps it simple and works for any framework.

## B. S3

**Q84 [B] What is an S3 "prefix" and does S3 have folders?**
No folders: keys are flat strings. `sites/alice/index.html` is one key whose prefix is `sites/alice/`. Policies, lifecycle rules and listings operate on prefixes, which feels like folders.
Study: [S3 organizing objects](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-prefixes.html)

**Q85 [B] List the main prefixes in the bucket and their purpose.**
`portal/` (portal pages, kit zip, MCP package; republished by the teacher), `sites/` (live student sites), `archive/` (offline sites), `uploads/` (temporary zips; expire in a day), `userdata/` (student stored files), `knowledge/` (books and indexes), `projects/` (synced source), `worker/` (the optional machine).
Repo: `docs/TECHNICAL_SPECIFICATION.md` section 3.2.

**Q86 [I] What does S3 versioning give and what does it cost?**
Overwrites and deletes keep older versions (recoverable via a delete marker removal). Costs storage for the old versions; a lifecycle rule expires them after 30 days. It also makes the bucket harder to delete: all versions and delete markers must be removed first (the installer's `emptyBucket`).

**Q87 [I] What is a delete marker?**
When you "delete" in a versioned bucket, S3 adds a marker that hides the object; the previous version still exists. Removing the marker brings the file back (`docs/TEACHER_SETUP.md` 13a).

**Q88 [I] Presigned PUT vs presigned POST: which does the project use for what?**
Zips for sites/backends: presigned PUT. Student stored files: presigned **POST** with a `content-length-range` policy condition, so S3 itself refuses oversized files. A PUT can't enforce size limits.
Study: [Presigned POST (browser uploads)](https://docs.aws.amazon.com/AmazonS3/latest/userguide/HTTPPOSTExamples.html)

**Q89 [I] Why upload directly to S3 rather than through the API?**
Lambda is billed per millisecond and has a 6 MB request limit; streaming big files through it is slow and costly. The Lambda only authorises (issues a URL) and later processes the stored object.

**Q90 [I] What S3 lifecycle rules exist and why?**
`ExpireUploadedZips` (delete `uploads/` after 1 day), `ExpireOldVersions` (noncurrent versions after 30 days, incomplete multipart uploads aborted after 2 days) and `CleanUpDeleteMarkers`. They are free automatic garbage collection.
Study: [S3 Lifecycle](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html)

**Q91 [D] How are published files given the right `Content-Type`?**
At publish the Lambda assigns types from file extensions (`content_type()` in `lambda/app.py`, using `mimetypes` plus a few extras) and a `CacheControl`, because CloudFront serves exactly what S3 stores as metadata. A wrong type (e.g. `.js` as `text/plain`) breaks the page.

**Q92 [B] Why is the artifact bucket (`<stack>-artifacts-<account>`) separate?**
It only holds the packaged Lambda zip the installer uploads for CloudFormation. Keeping deploy artifacts out of the data bucket avoids mixing lifecycles and permissions.
Repo: `docs/TEACHER_ARCHITECTURE.md` section 3.3.

## C. DNS, certificates and domains

**Q93 [B] What is DNS in two sentences, and what is a CNAME?**
DNS translates names to addresses. A CNAME says "this name is an alias of that other name" (e.g. `www.shop.com` → `xyz.cloudfront.net`), and the lookup continues at the target.
Study: [How DNS works (comic)](https://howdns.works/) and [Cloudflare: What is DNS?](https://www.cloudflare.com/learning/dns/what-is-dns/)

**Q94 [I] What are the three DNS records of a domain install and why do they come in order?**
(1) certificate validation CNAME (ACM proves you control the domain), (2) `*.<domain>` → sites distribution, (3) `deploy.<domain>` → portal distribution. (2) and (3) can't exist before the first deploy; and CloudFront refuses an alias while DNS points that name elsewhere, hence `PortalAlias=false` first, `true` after.
Repo: `docs/TEACHER_ARCHITECTURE.md` section 3.5.

**Q95 [I] What is ACM DNS validation?**
AWS Certificate Manager issues a TLS certificate after it can see a specific CNAME in your DNS (proof of ownership). Once issued it renews automatically as long as that record stays.
Study: [ACM DNS validation](https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html)

**Q96 [I] What is a wildcard certificate and what are its limits?**
`*.example.edu` covers one label level (`alice.example.edu`, not `a.b.example.edu`) and not the bare domain unless added. It lets every student site share one certificate.

**Q97 [I] Why do students bringing their own domain (e.g. `shop.com`) need something special?**
A wildcard under *your* domain can't cover *their* domain, and a single distribution can only have a limited set of aliases/certificates. CloudFront **multi-tenant distributions** let one template serve many customer domains, each as a "distribution tenant" with its own managed certificate.
Study: [CloudFront multi-tenant distributions](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/distribution-config-options.html) (search "distribution tenants" in the console docs for the latest page).
Repo: `lambda/domains.py`, `installer/tenant.mjs`.

**Q98 [I] What does the student's domain flow look like?**
Student enters a domain → platform stores a `domain#<name>` lock → student adds a CNAME to the platform's **routing endpoint** → platform polls (`waiting_dns → issuing_certificate → finishing → live`) → CloudFront creates the tenant once it can see the CNAME. Removal is the reverse. Each poll advances one step.
Repo: `lambda/domains.py` docstring.

**Q99 [I] Why is a `domain#<name>` lock item needed?**
Two students must not claim the same domain. As with slugs, a conditional put makes the claim atomic.

**Q100 [D] Why can't a CloudFormation template create tenants and connection groups?**
At the time of building, those CloudFront resource types were not usable (or reliable) in CloudFormation, so `installer/tenant.mjs` creates them with the AWS CLI. They are therefore "outside CloudFormation": `plan` must pass their ids as parameters each time, and `destroy` removes them first. Lesson: infrastructure-as-code covers most, not all, of the world.
Repo: `docs/TEACHER_ARCHITECTURE.md` section 7.4.

**Q101 [D] What happens to custom domains if an update omits the three ids?**
They would silently turn off. So `plan` reads the ids from the live stack, never from a possibly older saved state: "the stack is the truth".

**Q102 [D] Why does destroy delete tenants before the distribution?**
CloudFront dependencies: a tenant must be disabled, wait until Deployed, then deleted; only then can the distribution be disabled/deleted; the OAC can't be deleted while used. `destroy` encodes this order before `delete-stack`.

## D. Browser security and caching

**Q103 [I] What is the same-origin policy and how does it protect one student from another?**
Each student has a different *origin* (`alice.example.edu` vs `bob.example.edu`), so JavaScript on Bob's site cannot read Alice's cookies, localStorage or API responses. Using subdomains per student (rather than paths) is what gives this isolation for free.
Study: [MDN: Same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Same-origin_policy)

**Q104 [D] Why does path-mode hosting (`<dist>.cloudfront.net/alice/`) offer weaker isolation?**
All students share one origin, so one student's script can read another's localStorage. Path mode exists only when the teacher has no domain. A custom root domain is recommended for real classes. (Cookie scope also matters: a cookie set for `.example.edu` would be shared.)

**Q105 [I] Why must student apps never set cookies for the parent domain?**
A cookie with `Domain=.example.edu` is sent to every student subdomain; another student could read or fix it. The sign-in design keeps cookies host-only on the app's own address.

**Q106 [I] What is the HTTP cache lifecycle for `index.html` after publish?**
Browser/CDN may cache; the publish invalidates CloudFront; browsers may still keep a copy depending on headers. Hashed asset names plus a not-cached `index.html` is the standard approach.
Study: [web.dev: HTTP caching](https://web.dev/articles/http-cache)

## Study materials for this chapter

- AWS docs: **CloudFront Developer Guide** <https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/>
- **S3 user guide**: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html>
- Mozilla **MDN web security**: <https://developer.mozilla.org/en-US/docs/Web/Security>
- Julia Evans' zines on DNS and HTTP (<https://jvns.ca/>): short, visual, excellent for beginners.
- YouTube (search): "AWS re:Invent CloudFront deep dive", "How HTTPS works (ByteByteGo)", "Hussein Nasser HTTP/2 explained".
- In the repo: `docs/AWS_LEARNINGS.md` sections 1–3, then `template.yaml` `RouterFunction`, `SitesBucketPolicy`.
