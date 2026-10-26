# Custom domains for students

**Status:** set up automatically by the installer (`installer/tenant.mjs`): `install` does it by default, `custom-domains` repeats or retries it, and `destroy` removes it. This file keeps the background: what the pieces are and why, and what was learned when they were first made by hand.

The two CloudFront functions in this folder (`spa-fallback.js`, `tenant-api-route.js`) are published by the installer under the names `<stack>-spa-fallback` and `<stack>-tenant-api`. The multi-tenant distribution's configuration is generated in `installer/tenant.mjs` (`distributionConfig`). `apply-tenant-api-route.py` is only for a platform whose distribution was made by hand before the installer existed.

Goal: a teacher switch (on/off per student). When ON, the student can enter their own domain on the student portal page; when OFF the field is disabled with "Owner not enabled for you. Please contact site owner." One custom domain per student. The student adds one CNAME at their own DNS host; HTTPS is automatic.

## The pieces
| Piece | Made by |
|---|---|
| Two CloudFront functions (single-page-app fallback, the /api route) | the installer, from the two files in this folder |
| A connection group (its routing endpoint is what students CNAME to) | the installer; the portal's tenants join it (`CONNECTION_GROUP_ID`) |
| A multi-tenant ("tenant-only") distribution, origin path `/sites/{{student}}` and `/b/{{student}}` for `/api/*` | the installer |
| One tenant per student domain, with a CloudFront-managed certificate | the portal, at run time (`lambda/domains.py`) |
| The bucket policy that lets the distribution read the sites | `template.yaml`, from the `TenantDistributionId` parameter |

## Verified facts and cautions
- `create-distribution-tenant` fails (`InvalidArgument ... Could not verify Domain Name ownership`) until the domain's CNAME already points at the routing endpoint. So the flow is: student adds the CNAME, then the portal creates the tenant.
- With `ManagedCertificateRequest {ValidationTokenHost: cloudfront, PrimaryDomainName: <domain>}` CloudFront issues the certificate over HTTP (no validation record). It is created as a pending certificate and must then be applied to the tenant (`update-distribution-tenant --customizations Certificate={Arn=...}`); after that the domain becomes `active` and the site serves over HTTPS. The portal's state machine does this by polling.
- Bare (apex) domains cannot use a CNAME unless their DNS host flattens CNAMEs (Cloudflare does). Recommend `www.`.
- At Cloudflare, records that point at CloudFront must be **DNS only** (grey cloud).
- Tenant pricing: check the AWS billing console (a community report says the first tenants are free).
- Deleting a tenant: disable it, wait until `Deployed`, then delete. Deleting the distribution: disable, wait, delete. The stack's origin access control cannot be deleted while the distribution exists, so `destroy` removes the custom-domain pieces first.
- A fresh account has no connection groups at all until the first tenant exists; CloudFront then also creates a default one. The installer makes its own group (named after the stack) so the routing endpoint is known before any student uses the feature.
