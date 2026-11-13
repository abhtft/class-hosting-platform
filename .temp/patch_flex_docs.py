root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("CHANGELOG.md", "It creates one EC2 machine (c7g.xlarge, 4 ARM vCPUs and 8 GB, 30 GB encrypted disk, us-east-1)", "It creates one EC2 machine (m7i-flex.large, 2 vCPUs and 8 GB, x86, 30 GB encrypted disk, us-east-1; a size a Free plan account may use, the ARM c7g sizes need a paid-plan account)")
edit("CHANGELOG.md", "about $5 a month at 30 minutes a day (the machine only while it runs, the disk always).", "about $4 a month at 30 minutes a day (the machine only while it runs, the disk always).")
edit("docs/COMPUTE_WORKER_PLAN.md", "| Type | **c7g.xlarge** (4 ARM vCPUs, 8 GB). ARM matches the Lambda functions. Fallback `c7i.xlarge` only if a tool has no ARM build |",
     "| Type | **m7i-flex.large** (2 x86 vCPUs, 8 GB). Chosen because the account is on the AWS **Free plan**, which allows only free-tier instance types (found when the first apply of `c7g.xlarge` was refused and rolled back). On a paid-plan account the ARM sizes (`c7g.xlarge`, 4 vCPUs, 8 GB) can be chosen instead. The image (x86 or ARM) follows the type |")
edit("docs/COMPUTE_WORKER_PLAN.md", "| Compute, 30 minutes a day (about 15 hours a month) at about $0.145 an hour | about $2 to $3 |", "| Compute, 30 minutes a day (about 15 hours a month) at about $0.096 an hour (m7i-flex.large) | about $1.50 |")
edit("docs/COMPUTE_WORKER_PLAN.md", "| **Machine total** | **about $5 a month** |", "| **Machine total** | **about $4 a month** |")
edit("docs/COMPUTE_WORKER_PLAN.md", "| Machine | c7g.xlarge, 30 GB gp3, us-east-1 (as section 3) |", "| Machine | **m7i-flex.large** (changed 2026-11-07 from c7g.xlarge because the Free plan refused it; owner chose this), 30 GB gp3, us-east-1 |")
print("ok")
