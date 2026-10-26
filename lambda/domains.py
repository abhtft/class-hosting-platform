"""Custom domains for students, built on CloudFront multi-tenant "distribution tenants".

A teacher switches the feature on per token (item["domain_enabled"]). The student then enters one domain and adds a
CNAME to ROUTING_ENDPOINT at their own DNS host. State lives on the token item:

  domain, domain_state, tenant_id, domain_removing        (domain#<name> lock items keep names unique)

Setup is a state machine advanced by polling (GET /api/student/domain/status), one step per call:
  waiting_dns -> issuing_certificate -> finishing -> live        (removal: removing -> none)
CloudFront refuses to create the tenant until the CNAME is visible, so the first step simply retries.
"""
import os
import re

from botocore.exceptions import ClientError

import portal
from portal import PortalError, db

NOT_ENABLED = "Owner not enabled for you. Please contact site owner."
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

_client = None


def cloudfront():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client("cloudfront")
        if not hasattr(_client, "create_distribution_tenant"):
            raise PortalError(503, "This server's AWS library is too old for custom domains.")
    return _client


def tenant_distribution_id():
    return os.environ.get("TENANT_DISTRIBUTION_ID", "")


def validate_domain(raw):
    domain = str(raw or "").strip().lower().rstrip(".")
    if not DOMAIN_RE.match(domain):
        raise PortalError(400, "That is not a valid domain name. Example: www.example.com")
    root = portal.ROOT_DOMAIN
    if root and (domain == root or domain.endswith("." + root)):
        raise PortalError(400, f"Use your own domain. {root} names are already served for you.")
    if domain.endswith((".cloudfront.net", ".amazonaws.com")):
        raise PortalError(400, "Use your own domain, not an AWS address.")
    if domain.count(".") < 2:
        raise PortalError(400, "Use a subdomain such as www.example.com. A CNAME cannot be set on the bare domain.")
    return domain


def _save(item, remove=(), **fields):
    return db().update(item["pk"], fields, remove=remove)


def _state(item, state):
    return item if item.get("domain_state") == state else _save(item, domain_state=state)


# ---------- student actions ----------
def set_domain(item, body, now):
    if not tenant_distribution_id():
        raise PortalError(503, "Custom domains are not set up on this server.")
    if not item.get("domain_enabled"):
        raise PortalError(403, NOT_ENABLED)
    if item.get("domain"):
        raise PortalError(409, "You already have a custom domain. Remove it first.")
    if portal.status_of(item, now) != "active":
        raise PortalError(403, "You can set up a custom domain while your token is active.")
    domain = validate_domain(body.get("domain"))
    if not db().put({"pk": f"domain#{domain}", "token": item["id"]}, if_absent=True):
        raise PortalError(409, "That domain is already used by another site.")
    item = _save(item, remove=("tenant_id", "domain_removing"), domain=domain, domain_state="waiting_dns")
    try:
        return advance(item)
    except PortalError:
        _release(item)  # refused on the very first attempt: do not leave a saved domain the student must remove by hand
        raise


def advance(item):
    """Move setup forward by at most one step and return the updated token item. Safe to call repeatedly."""
    domain = item.get("domain")
    if not domain:
        return item
    if item.get("domain_removing"):
        return finish_removal(item)
    cf = cloudfront()
    tenant_id = item.get("tenant_id")

    if not tenant_id:
        try:
            tenant = cf.create_distribution_tenant(
                DistributionId=tenant_distribution_id(),
                Name=f"student-{item['slug']}",
                Domains=[{"Domain": domain}],
                Parameters=[{"Name": "student", "Value": item["slug"]}],
                ManagedCertificateRequest={"ValidationTokenHost": "cloudfront", "PrimaryDomainName": domain},
                Enabled=True,
            )["DistributionTenant"]
        except ClientError as e:
            error = e.response["Error"]
            message = error.get("Message", error.get("Code", ""))
            if error.get("Code") == "InvalidArgument" and "ownership" in message.lower():
                return _state(item, "waiting_dns")  # the CNAME is not visible yet
            raise PortalError(400, f"CloudFront refused this domain: {message}")
        return _save(item, tenant_id=tenant["Id"], domain_state="issuing_certificate")

    found = cf.get_distribution_tenant(Identifier=tenant_id)
    tenant = found["DistributionTenant"]
    cert_arn = ((tenant.get("Customizations") or {}).get("Certificate") or {}).get("Arn")
    active = any(d.get("Domain") == domain and d.get("Status") == "active" for d in tenant.get("Domains", []))
    if cert_arn and active and tenant.get("Status") == "Deployed":
        return _state(item, "live")
    if cert_arn:
        return _state(item, "finishing")

    details = cf.get_managed_certificate_details(Identifier=tenant_id)["ManagedCertificateDetails"]
    if str(details.get("CertificateStatus", "")).lower() == "issued":
        cf.update_distribution_tenant(
            Id=tenant_id, IfMatch=found["ETag"], Domains=[{"Domain": domain}],
            Customizations={"Certificate": {"Arn": details["CertificateArn"]}},
        )
        return _state(item, "finishing")
    return _state(item, "issuing_certificate")


def remove_domain(item):
    if not item.get("domain"):
        return item
    tenant_id = item.get("tenant_id")
    if not tenant_id:
        return _release(item)
    found = cloudfront().get_distribution_tenant(Identifier=tenant_id)
    if found["DistributionTenant"].get("Enabled"):
        cloudfront().update_distribution_tenant(Id=tenant_id, IfMatch=found["ETag"], Domains=[{"Domain": item["domain"]}], Enabled=False)
    item = _save(item, domain_removing=True, domain_state="removing")
    return finish_removal(item)


def finish_removal(item):
    """A tenant must be disabled and fully deployed before it can be deleted, so this is retried by polling and the sweeper."""
    cf = cloudfront()
    try:
        found = cf.get_distribution_tenant(Identifier=item["tenant_id"])
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if not (code == "EntityNotFound" or code.startswith("NoSuch")):
            raise
        found = None
    if found:
        tenant = found["DistributionTenant"]
        if tenant.get("Enabled") or tenant.get("Status") != "Deployed":
            return item
        cf.delete_distribution_tenant(Id=item["tenant_id"], IfMatch=found["ETag"])
    return _release(item)


def _release(item):
    db().delete(f"domain#{item['domain']}")
    return _save(item, remove=("domain", "domain_state", "tenant_id", "domain_removing"))


def finish_pending_removals():
    """Sweeper step: complete removals the student never came back to finish."""
    done, failed = [], []
    for item in db().list_prefix("tok#"):
        if item.get("domain_removing") and item.get("tenant_id"):
            try:
                if not finish_removal(item).get("domain_removing"):
                    done.append(item["slug"])
            except Exception as e:  # retried tomorrow
                failed.append(f"{item['slug']}: {e}")
    return done, failed


# ---------- teacher action ----------
def set_access(item, enabled):
    if not isinstance(enabled, bool):
        raise PortalError(400, "enabled must be true or false.")
    return _save(item, domain_enabled=enabled)
