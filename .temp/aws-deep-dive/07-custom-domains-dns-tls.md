# 07. DNS, TLS and custom domains (CloudFront multi-tenant)

Goal: understand every name and certificate involved when a request for `https://alice.apps.example.com` or a student's own `https://www.theirname.com` arrives, why the install needs DNS records, and how the multi-tenant machinery turns "add one CNAME" into a live HTTPS site.

---

## Part 1. DNS theory (the part most people only half understand)

### 1.1 How a name becomes an address

```
 browser -> stub resolver (OS) -> recursive resolver (ISP / 1.1.1.1 / 8.8.8.8)
                                     -> root servers      ("who runs .com?")
                                     -> TLD servers       ("who runs example.com?")
                                     -> AUTHORITATIVE servers for the zone ("what is www.example.com?")  <- your DNS host
```

- The **authoritative** servers hold the truth; everything else caches. Each answer carries a **TTL** (seconds to cache). That is why DNS changes "take time": caches hold the old answer until the TTL expires. **Negative answers are cached too** (NXDOMAIN is remembered for the zone's *SOA minimum* time): a name that did not exist a minute ago may keep "not existing" for a while.
- The authoritative servers for your domain are the **NS records** at your registrar (`dig NS example.com`). Querying them directly (`dig CNAME www.example.com @ns1.dnshost.com`) bypasses caches and shows the current truth: that is the check the guide recommends.

### 1.2 Record types you use

| Type | What | Example in this project |
|---|---|---|
| **CNAME** | "this name is an alias for that name" | `*.apps.example.com -> d111.cloudfront.net`; `deploy.apps.example.com -> d222.cloudfront.net`; a student's `www.theirname.com -> <routing endpoint>.cloudfront.net` |
| **A / AAAA** | name -> IPv4 / IPv6 address | CloudFront's own names resolve to these |
| **TXT** | free text (verification tokens) | (some validations use it) |
| **NS / SOA** | who is authoritative / zone metadata | at the registrar |

**CNAME rules that cause real incidents**

1. A name with a CNAME **cannot have any other record** (no MX, TXT, NS...). So **a CNAME cannot sit at the zone apex** (`theirname.com`, which must have NS and SOA). Students must use a subdomain (`www.`, `app.`). Some DNS hosts (Cloudflare "CNAME flattening", Route 53 "alias" records) fake an apex alias: they resolve it themselves and return A records.
2. **Wildcard** `*.apps.example.com` answers for any name **that has no explicit record of its own**; an explicit record always wins. That is why a separate `deploy` record can coexist with the `*` wildcard record.
3. DNS wildcards can match **more than one label** (`a.b.apps.example.com`), but **TLS wildcard certificates match exactly one label.** In your platform the router function rejects `a.b` (dots are not allowed in a student name), so the two rules agree.

### 1.3 The Cloudflare "orange cloud" trap

At Cloudflare each record is either **DNS only** (grey cloud: returns the real CNAME) or **Proxied** (orange cloud: Cloudflare answers with *its own* IPs and sits in front as a reverse proxy). For these records the proxy **hides the CNAME**:

- ACM's DNS validation and CloudFront's domain-ownership check look for the CNAME and **fail** if they get Cloudflare's IPs.
- You would also stack two CDNs and two TLS layers.

Rule: every record this platform needs must be **DNS only**.

---

## Part 2. TLS theory

- **TLS** secures the connection; a **certificate** binds a name to a public key and is signed by a **certificate authority**. The browser checks: the name matches, the chain leads to a trusted root, the dates are valid.
- **SNI** (Server Name Indication): the browser tells the server which name it wants *before* the handshake completes, so one IP address can present many certificates. CloudFront with `sni-only` (your choice) is free; "dedicated IP" certificates cost about $600 per month and exist only for ancient clients.
- **Wildcard certificate** `*.apps.example.com` covers `alice.apps.example.com` but **not** `apps.example.com` and **not** `a.b.apps.example.com`.
- **ACM** (AWS Certificate Manager) issues **free** public certificates for use with AWS services. **For CloudFront the certificate must be in `us-east-1`**: that is why the whole stack is in `us-east-1`.
- **DNS validation:** ACM gives you a CNAME like `_abc123.apps.example.com -> _def456.acm-validations.aws.`; adding it proves control. **Keep this record forever:** ACM **renews automatically** only while the validation record stays in place (certificates are valid about 13 months). A deleted validation record = a certificate that silently expires one year later.
- **Security policy `TLSv1.2_2021`** on the CloudFront distribution = minimum TLS 1.2 with modern ciphers.

---

## Part 3. What we built: the three certificates and three kinds of names

| Name | Certificate | Served by | Created by |
|---|---|---|---|
| `*.<root>` (student sites) | ACM wildcard cert (one, us-east-1) | `Distribution` | installer (`ensureCertificate`), you add the validation CNAME |
| `deploy.<root>` (portal) | the same wildcard cert | `PortalDistribution` | same; DNS record you add after the build |
| `www.theirname.com` (custom) | a **CloudFront-managed certificate** per student domain | a **distribution tenant** | the portal at run time |

### 3.1 The two-phase domain install (and why)

DNS records you add (guide 4.1): (1) the ACM **validation** CNAME; (2) `*.<root>` -> the student distribution; (3) `deploy.<root>` -> the portal distribution.

`PortalAlias` is `false` on the first deploy and `true` on the second (`finish-domain`). Reason from the template comment: **CloudFront refuses to attach an alias while DNS (for example the `*.<root>` wildcard) still points `deploy.<root>` at a different distribution.** So you build first, then create the DNS record for `deploy`, then attach the alias in a second pass. That is a real constraint of how CloudFront validates aliases.

### 3.2 CloudFront multi-tenant ("SaaS Manager") vocabulary

| Term | Meaning |
|---|---|
| **Multi-tenant distribution** (`ConnectionMode: tenant-only`) | A distribution that is a **template**: it holds the origins, behaviors and functions once. It serves no traffic by itself. |
| **Distribution tenant** | One per customer domain. Has its own domain(s), certificate and **parameter values**, and inherits everything else from the template. Created at run time through the API. |
| **Parameters** | Placeholders in the template, here `{{student}}`, substituted per tenant (`student = alice`). The template's origin path is `/sites/{{student}}` and `/b/{{student}}`, so each tenant reads only its own folder and backend. |
| **Connection group** | A set of CloudFront edge routing with its own **routing endpoint** (`xxxx.cloudfront.net`). Students CNAME to this one name. (Creating your own group, as the installer does, gives a stable endpoint.) |
| **Managed certificate** | CloudFront requests and renews a certificate for the tenant's domain (`ManagedCertificateRequest`, with `ValidationTokenHost: cloudfront`: the validation token is hosted by CloudFront itself because the domain already points at it, so **no extra validation record** is needed). |

**Cost:** tenants are billed (a per-tenant fee on top of request and data charges): this is the number the teacher guide says to **verify** in your billing console. **Verify** the current pricing before offering custom domains to a big class.

### 3.3 The custom-domain state machine (`lambda/domains.py`)

```
none --(student enters domain; teacher allowed it)--> waiting_dns
waiting_dns --(CNAME visible; tenant created)--> issuing_certificate
issuing_certificate --(managed cert issued; tenant updated with its ARN)--> finishing
finishing --(domain active + certificate attached + tenant Deployed)--> live
any --(student removes)--> removing --(tenant disabled -> Deployed -> deleted)--> none
```

- The page **polls** `GET /api/student/domain/status`; **each call advances at most one step** (the portal is not allowed to hang for minutes, and a poll is cheap and idempotent).
- **Why `waiting_dns` is just a retry:** CloudFront **refuses `CreateDistributionTenant` until the CNAME is visible** (ownership check). The code catches that specific error (`InvalidArgument` mentioning "ownership") and stays in `waiting_dns`; any other error becomes `CloudFront refused this domain: ...`.
- **Locks:** `domain#<name>` (conditional put) so a domain belongs to one student; `slug#...` is separate.
- **Removal order is forced by CloudFront:** a tenant must be **disabled, fully Deployed, then deleted**; `finish_removal` is retried by polling and by the **daily sweeper** ("a tenant must be disabled and fully deployed before it can be deleted").
- **Cache:** a tenant has its **own cache**; the publish step calls `create_invalidation_for_distribution_tenant` (failure there does not fail the deploy).

### 3.4 The DNS hint (new in 2.0)

While in `waiting_dns` the portal calls `lookup_cname(domain)`: an **HTTPS DNS-over-HTTPS** query to `cloudflare-dns.com/dns-query?name=<domain>&type=CNAME` (JSON format, 2 s timeout). It compares the answer to the routing endpoint and reports `ok`, `missing` or `wrong` (with *what it points at* and *what it must point at*). Notes: a Lambda has no `dig`, hence DoH; **the student's domain name is sent to a third-party resolver** (Cloudflare) as part of this check: a small privacy point worth knowing; if the lookup fails the hint is simply absent.

---

## Part 4. Console steps (Mode B)

1. **Certificate Manager (ACM)** -> region **N. Virginia** -> **List certificates**: the wildcard `*.<root>`. Open it: **Status** *Issued*, **Domains** table shows the validation CNAME (name and value) and **Validation status** *Success*, **In use** (associated resources), **Renewal eligibility** *Eligible*.
2. **CloudFront** -> the two distributions -> **General** -> **Alternate domain names** (`*.<root>`; `deploy.<root>`) and **Custom SSL certificate** (the ACM cert).
3. **CloudFront** -> look for the multi-tenant section (labels vary by console version: **SaaS Manager**, **Multi-tenant distributions**, **Distribution tenants**, **Connection groups**; **verify** where yours shows them): the connection group (its **routing endpoint**), the tenant-only distribution, and one **tenant** per student domain with its **status** and **certificate**.
4. **CloudFront** -> **Functions**: `<stack>-spa-fallback` and `<stack>-tenant-api` (the custom-domain versions of the router and the API route function).
5. Your **DNS host** (Cloudflare, etc.): confirm the three records, all **DNS only**.

---

## Part 5. CLI verification

```bash
# Certificates (us-east-1)  [READ]
aws acm list-certificates --query 'CertificateSummaryList[].[DomainName,Status,CertificateArn]' --output table
aws acm describe-certificate --certificate-arn <arn> \
  --query 'Certificate.[Status,RenewalEligibility,InUseBy,DomainValidationOptions[0].ResourceRecord]'

# Multi-tenant pieces (needs a recent AWS CLI v2: run `aws --version`; if a command is missing, upgrade the CLI)  [READ]
aws cloudfront list-connection-groups
aws cloudfront list-distribution-tenants
aws cloudfront get-distribution-tenant --identifier <tenant id> --query 'DistributionTenant.[Domains,Status,Customizations]'

# The two functions used by tenants  [READ]
aws cloudfront list-functions --query "FunctionList.Items[?contains(Name,'spa-fallback') || contains(Name,'tenant-api')].Name"
```

### 5.1 DNS and TLS experiments (no AWS credentials needed)

```bash
# D1. Does the wildcard answer?  Does the explicit portal record win?
dig +short CNAME alice.<root>          # the wildcard's target (student distribution)
dig +short CNAME deploy.<root>         # the explicit record's target (portal distribution): different!

# D2. Truth vs cache: ask the authoritative server directly
dig NS <root-domain>                   # find the nameservers
dig +short CNAME deploy.<root> @<one of those nameservers>

# D3. What certificate does the wildcard present?
openssl s_client -connect alice.<root>:443 -servername alice.<root> </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
#  EXPECT: subjectAltName DNS:*.<root>, an Amazon issuer, notAfter about 13 months from issue

# D4. A wildcard covers ONE label: the cert does not match a two-label name
curl -sS -o /dev/null -w "%{http_code}\n" https://a.b.<root>/   # EXPECT a TLS error or a DNS failure (not a clean 200)

# D5. Reproduce the portal's own DNS-over-HTTPS hint query
curl -s -H 'accept: application/dns-json' 'https://cloudflare-dns.com/dns-query?name=www.<student-domain>&type=CNAME'
#  "Answer" with "type": 5 = a CNAME record; no "Answer" = no CNAME visible (this is the 'missing' state)

# D6. Check a student's custom domain end to end
curl -sI https://www.<student-domain>/ | head -5
openssl s_client -connect www.<student-domain>:443 -servername www.<student-domain> </dev/null 2>/dev/null | openssl x509 -noout -issuer -subject -dates
```

### 5.2 Walk the state machine (needs a test student with Custom domain on)

1. Student enters `hint1.<your-test-domain>` **before** creating any record: the card says "Waiting for your DNS record" and the hint says **missing**. (`PUT` returns state `waiting_dns`.)
2. Add the CNAME to the **site** address (the wrong one): the hint turns **wrong** ("points at X but must point at <routing endpoint>").
3. Fix it to the routing endpoint: states `issuing_certificate` -> `finishing` -> `live` (watch with `curl -s -H "authorization: Bearer <student session>" https://deploy.<root>/api/student/domain/status`).
4. Remove the domain on the student page and watch `removing` -> none.
5. Look at CloudTrail (Event history) for `CreateDistributionTenant`, `UpdateDistributionTenant`, `DeleteDistributionTenant`: the portal's role session is the caller.

---

## Part 6. Design notes an architect should flag

1. **Dangling DNS / subdomain takeover.** After a student removes their domain in the portal, the `domain#<name>` lock is released, but if they **forget to delete their CNAME** it still points at your routing endpoint. If *another* student then registers that same name, the ownership check (CNAME points at the group's routing endpoint) passes and the second student's site would be served on the first student's domain. This is the classic "dangling CNAME" takeover. The teacher guide tells students to delete the CNAME after removal; the platform cannot enforce it. **Verify** whether CloudFront's multi-tenant ownership check also requires a TXT challenge in your account; if not, this is a residual risk to document or mitigate (for example: refuse to re-register a recently released domain, or require a DNS TXT token tied to the student).
2. **Cookie and origin isolation between sibling subdomains.** All student sites share the registrable domain `<root>`. Browsers isolate **origins** (`alice.` vs `bob.` are different origins), but **cookies can be set for the parent domain** (`Domain=.apps.example.com`) by one student's JavaScript and are then sent to the others (and to `deploy.`). The portal uses bearer tokens in `sessionStorage`, not cookies, so it is not exposed, but student apps that use cookies could collide. Using a **dedicated domain** for the platform (not one that hosts other apps or login cookies) avoids this. See also the **Public Suffix List** (domains like `github.io` are listed so browsers treat each subdomain as separate: yours is not).
3. **Certificate renewal depends on a DNS record you could delete by accident** (the ACM validation CNAME). Put a note next to it in your DNS host.
4. **Quotas.** ACM has a default limit on certificates per account (verify); CloudFront has limits on tenants/distributions and on alternate domain names per distribution (verify in **Service Quotas**).

---

## Part 7. What you should now be able to explain

1. Why can't a student put `theirname.com` (the bare domain) as a CNAME, and what do some DNS hosts do about it? (CNAME exclusivity at the apex; flattening/alias records.)
2. Why must every record be DNS only on Cloudflare? (The proxy hides the CNAME that CloudFront and ACM look for.)
3. What is the difference between the wildcard certificate and a tenant's managed certificate, and who requests each? (Installer/ACM vs the portal/CloudFront.)
4. Why does the install add `deploy.<root>` in a second pass? (CloudFront will not attach an alias while DNS points it at another distribution.)
5. Why does each poll advance only one step? (No long-running Lambda; idempotent; cheap.)
6. What breaks in a year if you delete the ACM validation CNAME? (The certificate fails to auto-renew and expires.)
7. What does `{{student}}` in the tenant's origin path do, and why is it a security boundary? (The tenant can only read its own `sites/<student>/`.)
8. Describe the dangling-CNAME takeover and two ways to reduce it.
