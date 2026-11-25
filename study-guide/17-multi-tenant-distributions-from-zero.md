# Chapter 17: Multi-tenant distributions, explained from zero

> For a reader who is new to networking. No step assumes the one before it was understood: if a step feels fast, go back one. Each part ends with a **Check yourself** box. This chapter explains the idea behind `lambda/domains.py` and `installer/tenant.mjs`, and the questions Q97 to Q102 of Chapter 3.
> Facts about this repository are marked **Repo**. Facts about AWS come from the AWS documentation (linked at the end); where a detail is not confirmed there, it is marked **verify**.

---

## 0. The one-minute version

- A website lives on a **server**. People reach it by a **name** (`shop.com`) that the internet turns into a numeric **address**. The connection is encrypted with a **certificate** that must match the name.
- A **CDN** (CloudFront) is a worldwide network of servers that keep copies of your pages near the visitor. In CloudFront, one configuration of this network is called a **distribution**.
- Normal rule: one distribution holds a short list of names. That is fine for one business with a few domains. It is not fine for a platform where **hundreds of students each bring their own domain**.
- A **multi-tenant distribution** fixes this: you write **one template** (where the pages come from, caching, security) and then add a lightweight **tenant** per customer domain. Each tenant has its own name and certificate, and inherits the template. One template, many customers.
- The student's only job: add one **CNAME** record at their own DNS host that points their domain to the platform's address. The platform does the rest.

If that paragraph is clear, jump to section 6. If not, read on in order.

---

## 1. How a web page reaches you (the six words you must know)

### 1.1 Address, port, packet
Every computer on the internet has an **IP address** (like `203.0.113.7`). A **port** says which program on that computer to talk to (web = 443 for secure, 80 for plain). Data travels in small **packets**. You never need to handle these; you need to know that "a server" is "an IP address plus a program listening on a port".

### 1.2 Name and DNS
Humans use names (`shop.com`). **DNS** (Domain Name System) is the internet's phone book: you ask "what is the address of `shop.com`?" and a DNS server answers. Where the answers live is controlled by whoever **hosts the DNS** for that domain (the registrar, Cloudflare, Route 53, GoDaddy...). The owner of the domain edits records there.

The record types you meet in this project:

| Record | Meaning | Example |
|---|---|---|
| **A** | name to an IPv4 address | `shop.com  A  203.0.113.7` |
| **CNAME** | name is an **alias** of another name; the lookup continues at the target | `www.shop.com  CNAME  d111abc.cloudfront.net` |
| **TXT** | free text, often used to prove ownership | `_verify.shop.com TXT "token123"` |

Rules to remember: (1) A CNAME cannot sit on the bare domain (`shop.com`) in classic DNS, only on subdomains (`www.shop.com`): that is why this platform asks students for a subdomain (**Repo**: `validate_domain` says "A CNAME cannot be set on the bare domain"). (2) DNS answers are **cached** for a while (the TTL), so a change can take minutes to be visible.

### 1.3 HTTP and the Host header
A browser opens a connection and sends a request such as `GET /cart` together with a line `Host: shop.com`. **The Host header says which website it wants.** One server can host many sites and picks the right one by this line. This is the first step of multi-tenancy ("many sites behind one door"): the server looks at the name and decides.

### 1.4 HTTPS, TLS and certificates
HTTPS is HTTP inside an encrypted tunnel (**TLS**). To be sure the server is really `shop.com`, it shows a **certificate**: a document, signed by a trusted authority (a **CA**), that says "this key belongs to `shop.com`". The browser checks: signed by a CA I trust, not expired, and **the name in it matches the name I typed**. Otherwise you see the red warning page.

A certificate covers specific names. A **wildcard** certificate `*.example.edu` covers `alice.example.edu` and `bob.example.edu` (one level only), not `shop.com`. A name that is not in the certificate cannot be served securely. Keep this fact: it is the whole reason multi-tenant distributions exist.

### 1.5 SNI: how one IP address serves many certificates
The certificate is needed **before** the browser can send its Host header (the tunnel does not exist yet). So the browser first says, in clear text, "I want `shop.com`" (this is **SNI**, Server Name Indication), and the server then picks the matching certificate. Without SNI, one IP could show only one certificate. With SNI, a CDN can serve thousands of domains from the same addresses.

### 1.6 CDN and edge
A **CDN** (Content Delivery Network) puts servers in hundreds of cities (the **edge**). A visitor in Patna connects to a nearby edge server, not to your origin in Virginia. The edge keeps a **cached** copy of pages, so most requests never reach your origin. The origin (where the real files live, here an S3 bucket or an API) is only asked when the edge has no fresh copy.

**Check yourself 1**
1. What does the Host header tell the server? What does SNI tell it, and why is SNI needed?
2. Why can `*.example.edu` not secure `shop.com`?
3. Why must a student use `www.shop.com` and not `shop.com` for a CNAME?

*(Answers: 1. which site is wanted; SNI carries the name before encryption starts so the right certificate is chosen. 2. A certificate lists the names it covers; `shop.com` is not under `example.edu`. 3. Classic DNS does not allow a CNAME on the bare domain.)*

---

## 2. CloudFront in plain words (a single-tenant world)

In AWS CloudFront you create a **distribution**. It holds:

- **Aliases** (also called alternate domain names): the names it answers to, for example `www.shop.com`. The visitor's name must be in this list.
- **A certificate** (from AWS Certificate Manager, **ACM**) that covers those aliases.
- **Origins**: where the content comes from (an S3 bucket path, an API address).
- **Behaviors**: rules by URL path. Example: `/api/*` go to the backend, no caching; everything else goes to S3, cached.
- **Functions at the edge**: tiny programs that can rewrite a request (this platform's `RouterFunction` turns `alice.example.edu/cart` into `/sites/alice/cart`).
- **Security**: who may fetch from the origin (here an **OAC**, so the bucket is private and only CloudFront may read it).

CloudFront gives the distribution its own name like `d111abc.cloudfront.net`. To use `www.shop.com`, the owner adds `www.shop.com CNAME d111abc.cloudfront.net` and `www.shop.com` is added to the aliases with a certificate that covers it.

**This platform already does this for student sites under the teacher's own domain:** one wildcard certificate `*.example.edu`, one distribution, and the `RouterFunction` reads the student's name from the Host header. **Repo**: `template.yaml` (`Distribution`, `RouterFunction`), Chapter 1 Q11, Chapter 3 section A.

**Check yourself 2**
1. Which three things must contain `www.shop.com` for it to work on a distribution (DNS, a list in CloudFront, a certificate)?
2. Why is the S3 bucket kept private?

---

## 3. The problem: a student brings their own domain

A student wants `www.shop.com` (their own domain, not under the teacher's). Using only what section 2 gives you:

1. Add `www.shop.com` to the aliases of the existing distribution. But a distribution accepts only a **limited number** of aliases and **one certificate** (or a few). A wildcard under the teacher's domain cannot cover `shop.com`.
2. Create a **new distribution per student**. That works for ten students. For hundreds it means hundreds of near-identical configurations to create, update, monitor and pay for. A fix to the shared setup must be repeated in each one. Distributions are also slow to deploy (minutes each) and AWS limits how many an account may have.

Both ways break. This is the classic **SaaS problem**: many customers, each with their own domain, sharing one product.

---

## 4. The idea of multi-tenancy (names and pictures)

**Tenant** = one customer of a shared system. **Multi-tenant** = one system serving many tenants, each isolated from the others.

Three common shapes (**Repo**: Chapter 1 Q4):

```
Silo      one house per family         (a full copy of the system per tenant)
Pool      one apartment block          (all tenants share everything, kept apart by labels and rules)
Bridge    a block with some private    (shared where cheap, separate where it matters)
          rooms
```

A silo gives the strongest separation and the highest cost and effort. A pool is cheap and simple to operate, but its isolation is only as good as your labels and checks. The student-hosting platform is a **bridge**: one shared bucket, one database, one gateway, with separation by keys (`sites/<student>/...`, `tok#...`) and checks in code.

For the **edge**, CloudFront's multi-tenant distribution is exactly a pool with a clean way to give each tenant its own name, certificate and settings.

---

## 5. How CloudFront's multi-tenant model is built (the four parts)

AWS calls the feature **CloudFront SaaS Manager**. It introduces four things.

### 5.1 Multi-tenant distribution (the template)
A distribution that is **only a template**: origins, behaviors, caching, security, functions. It cannot serve traffic by itself and has no address of its own. Everything in it is inherited by the tenants.

### 5.2 Parameters (the blanks in the template)
A template needs blanks that each tenant fills in. A **parameter** is a named placeholder such as `{{student}}`. You can use parameters, for example, in an origin path. **Repo**: the template's origins are `OriginPath: '/sites/{{student}}'` for the pages and `/b/{{student}}` for the backend (`installer/tenant.mjs`, `distributionConfig`). A tenant for Alice sets `student = alice`, so her requests go to `/sites/alice` in the bucket. One template, and the **tenant's parameter picks which student's files** are served. (AWS documents a small limit on how many parameters you may define; check the current quota page before designing around it: **verify**.)

### 5.3 Distribution tenant (one customer's front door)
A **tenant** is small: a **domain** (`www.shop.com`), a **certificate** for it, and the **parameter values** (`student = alice`). It inherits everything else from the template. You create and delete tenants quickly and in large numbers.

**Repo**: `tenant_arguments(slug, domain)` in `lambda/domains.py`:

```python
DistributionId=<the template>,
Name=f"student-{slug}",
Domains=[{"Domain": domain}],
Parameters=[{"Name": "student", "Value": slug}],
ManagedCertificateRequest={"ValidationTokenHost": "cloudfront", "PrimaryDomainName": domain},
Enabled=True,
```

Read it line by line: which template, a name, which domain, which student, and "please get a certificate for this domain for me" (a **managed certificate**: AWS issues and renews it).

### 5.4 Connection group and routing endpoint (the single address everyone points to)
Tenants need one address for DNS to point at. A **connection group** controls how viewers connect to CloudFront, and it has a **routing endpoint**: a name like `d111abcdef8.cloudfront.net`. AWS creates a default connection group; you may make your own to isolate some tenants. Every tenant's domain is pointed (by CNAME) at the group's routing endpoint, and CloudFront looks at the name the visitor typed (SNI/Host) to know **which tenant** it is.

```
www.shop.com   --CNAME-->  dXXXX.cloudfront.net  (the routing endpoint)
www.cafe.in    --CNAME-->  dXXXX.cloudfront.net  (the same one)
                                  |
                CloudFront reads the name the visitor used
                                  |
          tenant "student-shop" (student=shop)   tenant "student-cafe" (student=cafe)
                                  \                       /
                         the one multi-tenant distribution (the template)
```

**Repo**: the installer makes its own connection group because a fresh account has none until the first tenant exists, and the portal passes `ConnectionGroupId` when it creates a tenant (`docs/TEACHER_ARCHITECTURE.md` section 3.5 and 7.4).

**Check yourself 5**
1. In one sentence each: template, parameter, tenant, connection group.
2. If 300 students bring domains, how many templates and how many tenants are there? (1 and 300.)
3. What stops Alice's domain from showing Bob's site? (The tenant's parameter value; the template path is built from it.)

---

## 6. The whole journey of one student's domain

This is the state machine in `lambda/domains.py` (`waiting_dns` → `issuing_certificate` → `finishing` → `live`).

```
 Student                 Portal (this platform)                    DNS host              AWS CloudFront
   |  "my domain is        |                                          |                        |
   |   www.shop.com"  -->  | 1. validate the name (valid, not ours,   |                        |
   |                       |    a subdomain), claim a lock            |                        |
   |                       |    item  domain#www.shop.com             |                        |
   |  <-- "add this CNAME  |    (so two students cannot claim it)    |                        |
   |   to <routing endpoint>"                                         |                        |
   |  adds CNAME  ------------------------------------------------>   | stores the record      |
   |                       | 2. waiting_dns: polls; CloudFront        |                        |
   |                       |    refuses to create the tenant until    |                        |
   |                       |    the CNAME is visible --------------------------------------->    |
   |                       | 3. creates the tenant (domain, student=..,                         |
   |                       |    managed certificate request) ----------------------------------> |
   |                       | 4. issuing_certificate: AWS validates the name and issues the cert  |
   |                       | 5. finishing -> live                                                 |
   |  visits https://www.shop.com  ---------------------------------------------------------->    |
   |                       |                                          |   edge: SNI www.shop.com
   |                       |                                          |   -> tenant -> student=shop
   |                       |                                          |   -> /sites/shop in the bucket
```

Why each step exists:
- **The lock `domain#<name>`**: two students must not claim the same domain. It is created with a conditional write ("create only if absent"), which makes the claim atomic (**Repo**: Chapter 3 Q99).
- **The CNAME first**: AWS only creates the tenant once it can see that the domain really points to it (this proves control of the name and avoids certificates for names nobody owns).
- **The managed certificate**: the student never handles a certificate. AWS requests it, validates it, installs it and renews it.
- **`dns_hint`**: the most common student mistake is pointing the CNAME at the platform's *site* address instead of the *custom-domain* address; the portal looks at what DNS really returns (using DNS over HTTPS) and says exactly what to change.

**Check yourself 6**
1. Why does the portal create the `domain#...` lock before anything else?
2. A student's CNAME points to the platform's normal site address. What happens and where does the portal say so?

---

## 7. What is "outside CloudFormation" and why (so the install makes sense)

CloudFormation (`template.yaml`) describes the whole platform as code. At the time this was built, **tenants and connection groups** could not be created reliably through it, so `installer/tenant.mjs` creates them with the AWS CLI. Consequences (**Repo**: `docs/TEACHER_ARCHITECTURE.md` section 7.4):

- An install has **three phases** for custom domains: deploy the stack, create the connection group and the multi-tenant distribution with the CLI, then update the stack with the three ids (`TenantDistributionId`, `ConnectionGroupId`, `RoutingEndpoint`) so the portal knows them.
- A later stack update that forgot the three ids would silently turn custom domains off; so the installer reads them from the **live stack**, "the stack is the truth".
- Removing everything has a forced order imposed by AWS: tenants (disable, wait until Deployed, delete), then the distribution, then the group, then the stack.

If you remember one thing: **things created outside the template must be tracked by hand**, because the template does not know they exist.

---

## 8. Limits, costs and failure modes (what can go wrong)

| Symptom | Likely cause | Where to look |
|---|---|---|
| Student stays at `waiting_dns` | CNAME missing, wrong target, proxied (orange cloud on Cloudflare), or still cached | `dns_hint` message in the portal; `dig CNAME www.shop.com` |
| Certificate warning in the browser | Tenant not yet live, or the name does not match | State is not `live`; wait; check the name spelled in the tenant |
| Student sees another student's site | A wrong `student` parameter on the tenant | The tenant's parameter value, `domains.tenant_arguments` |
| Custom domains stopped after an update | The three ids were dropped from the stack parameters | Installer `plan` should read them from the live stack |
| Delete of the distribution is refused | Tenants still exist or not yet Deployed | Disable and delete tenants first |

Cost: CloudFront SaaS Manager is charged per tenant beyond a free allowance; one secondary source quotes a monthly per-tenant fee past 200 tenants, but **verify on the official CloudFront pricing page** before quoting a number to anyone.

---

## 9. A small experiment to feel it (no AWS needed)

On your own computer, in PowerShell or a terminal:

1. `nslookup www.google.com` (or `dig www.google.com`): you see the A record and sometimes a CNAME chain. This is DNS.
2. `curl -sI https://example.com`: you see the response headers. Notice `server:` and any `via:` or `x-cache:` lines: those come from CDNs when present.
3. In your browser, click the padlock on any site and open the certificate. Find the list of **Subject Alternative Names**: those are the names the certificate covers. Find one with `*.` in it.
4. `curl -sI --resolve example.com:443:<some-ip> https://example.com` is a way to test "same IP, different name" (SNI); skip it until step 1 to 3 are comfortable.

---

## 10. Glossary

| Term | Short meaning |
|---|---|
| **DNS** | The internet's name-to-address phone book |
| **CNAME** | A DNS alias from one name to another name |
| **TTL** | How long a DNS answer may be cached |
| **TLS / HTTPS** | The encryption of web traffic; the certificate proves the server's name |
| **Certificate (cert)** | A signed document: this key belongs to these names |
| **Wildcard certificate** | Covers `*.example.edu` (one level) |
| **SNI** | The visitor says the name before encryption so the right certificate is chosen |
| **CDN / edge** | Many servers near visitors that cache your content |
| **Origin** | Where the real content lives (S3, an API) |
| **Distribution** | One CloudFront configuration |
| **Alias** | A domain name a distribution answers to |
| **OAC** | CloudFront's permission to read a private bucket |
| **Multi-tenant distribution** | A CloudFront template shared by many tenants; serves nothing by itself |
| **Parameter** | A named blank in the template that each tenant fills in |
| **Distribution tenant** | One customer's domain, certificate and parameters on top of the template |
| **Connection group** | Controls how viewers connect; has the routing endpoint |
| **Routing endpoint** | The address (`d....cloudfront.net`) that all tenant domains CNAME to |
| **Managed certificate** | A certificate AWS requests, validates, installs and renews for you |
| **Silo / pool / bridge** | Separate per tenant / shared by all / a mix |

---

## 11. Exercises (answers are in the repo)

1. Open `installer/tenant.mjs`, find `distributionConfig`, and list: the two origins, their paths with `{{student}}`, and the header that protects the backend origin. Explain how one template serves every student.
2. Open `lambda/domains.py`. Draw the four states of `set_domain` on paper and write which function moves from one state to the next.
3. Why does `validate_domain` refuse names ending in `.cloudfront.net` and names under the platform's own root domain? (Think: what would a student be able to claim?)
4. Write the DNS record a student at Cloudflare must add for `www.cafe.in`, and say why "DNS only" (not proxied) is needed.
5. Imagine 1,000 students. Compare the silo (a distribution each), the pool (one distribution, many aliases) and the multi-tenant model in one table: effort to create, effort to change the shared setup, certificates, cost.
6. Which parts of this feature are in `template.yaml` and which are not? What breaks if the three ids are lost?

---

## 12. Where to learn more (in a sensible order)

**Start here (free, short):**
- *How DNS works* (illustrated comic): https://howdns.works/
- Cloudflare Learning Center: *What is DNS?*, *What is a CDN?*, *What is an SSL certificate?*, *What is SNI?* https://www.cloudflare.com/learning/
- Julia Evans' zines *Networking! ACK!* and *How DNS Works*: https://wizardzines.com/
- MDN: *How the web works* and *HTTP overview*: https://developer.mozilla.org/en-US/docs/Learn/Getting_started_with_the_web/How_the_Web_works

**Then the AWS pages for this feature:**
- *Understand how multi-tenant distributions work*: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/distribution-config-options.html
- *Create a custom connection group (optional)*: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/custom-connection-group.html
- *Amazon CloudFront SaaS Manager* (announcement): https://aws.amazon.com/blogs/aws/reduce-your-operational-overhead-today-with-amazon-cloudfront-saas-manager
- *Scale your SaaS application at the edge with CloudFront SaaS Manager*: https://aws.amazon.com/blogs/networking-and-content-delivery/scale-your-saas-application-at-the-edge-with-the-new-amazon-cloudfront-saas-manager
- CloudFront pricing (check the SaaS Manager section): https://aws.amazon.com/cloudfront/pricing/
- AWS Well-Architected *SaaS Lens* and the whitepaper on tenant isolation (the silo, pool, bridge ideas): https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/saas-lens.html

**Books (when you want the full picture):**
- *High Performance Browser Networking*, Ilya Grigorik (free online at https://hpbn.co): TCP, TLS, HTTP, in practical language.
- *Computer Networking: A Top-Down Approach*, Kurose and Ross: the standard textbook; read chapters 2 (application layer, DNS, HTTP) and 8 (security, TLS) first.
- *Designing Data-Intensive Applications*, Martin Kleppmann: for the data side of multi-tenancy later.

**How to use this chapter:** read sections 1 and 2 with the experiment of section 9 in front of you; read 3 to 5 once, slowly; then read section 6 next to `lambda/domains.py` and keep the diagram open. When the exercises of section 11 feel easy, the feature is yours.
