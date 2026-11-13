import re

root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p.replace("/", "\\"), encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p.replace("/", "\\"), "w", encoding="utf8").write(s)


def sub(p, a, b):
    s = read(p)
    assert a in s, (p, a[:70])
    write(p, s.replace(a, b, 1))


# ---------------------------------------------------------------- template: the caps become parameters (defaults = today's values)
sub("template.yaml", "  KnowledgeMonthlyCapUsd:", """  BackendInflightGlobal:
    Type: Number
    Default: 3
    MinValue: 1
    MaxValue: 500
    Description: >
      Student backend requests that may run at once on the whole platform. Every request can hold up to four function executions (the gateway, the student's
      function, and the knowledge or AI function it calls), so this protects the AWS account's concurrency limit (10 on a new account, which allows 3).
      The installer sets it from the account's real limit; raise the limit in Service Quotas (AWS Lambda, Concurrent executions) for a class.
  BackendInflightPerStudent:
    Type: Number
    Default: 2
    MinValue: 1
    MaxValue: 50
    Description: Requests that may run at once for one student's backend. Set by the installer from the account's limit.
  BackendApiRate:
    Type: Number
    Default: 10
    MinValue: 1
    MaxValue: 2000
    Description: Requests a second the student backend API accepts (its own throttle, so student traffic cannot starve the portal). Set by the installer.
  BackendApiBurst:
    Type: Number
    Default: 20
    MinValue: 1
    MaxValue: 4000
    Description: The short burst the student backend API accepts above its rate. Set by the installer.
  BackendWaitMs:
    Type: Number
    Default: 0
    MinValue: 0
    MaxValue: 1500
    Description: >
      When every slot is busy, how long (milliseconds) the gateway waits for one to free up before answering "busy". 0 answers at once. The installer turns
      this on only when the account has enough concurrency (a waiting gateway holds an execution itself).
  KnowledgeMonthlyCapUsd:""")
sub("template.yaml", "        INFLIGHT_GLOBAL: '3'\n        INFLIGHT_STUDENT: '2'", "        INFLIGHT_GLOBAL: !Ref BackendInflightGlobal\n        INFLIGHT_STUDENT: !Ref BackendInflightPerStudent\n        INFLIGHT_WAIT_MS: !Ref BackendWaitMs")
sub("template.yaml", "      DefaultRouteSettings:\n        ThrottlingBurstLimit: 20\n        ThrottlingRateLimit: 10\n\n  GatewayFunction:", "      DefaultRouteSettings:\n        ThrottlingBurstLimit: !Ref BackendApiBurst\n        ThrottlingRateLimit: !Ref BackendApiRate\n\n  GatewayFunction:")

# the specification check follows the parameters
sub("tests/test_spec.py", """check("gateway.inflightGlobal (template Globals)", str(g["inflightGlobal"]), re.search(r"INFLIGHT_GLOBAL: '(\\d+)'", globals_).group(1))
check("gateway.inflightPerStudent (template Globals)", str(g["inflightPerStudent"]), re.search(r"INFLIGHT_STUDENT: '(\\d+)'", globals_).group(1))""",
    """check("gateway.inflightGlobal (template parameter default)", str(g["inflightGlobal"]), re.search(r"  BackendInflightGlobal:\\n    Type: Number\\n    Default: (\\d+)", template).group(1))
check("gateway.inflightPerStudent (template parameter default)", str(g["inflightPerStudent"]), re.search(r"  BackendInflightPerStudent:\\n    Type: Number\\n    Default: (\\d+)", template).group(1))
assert "INFLIGHT_GLOBAL: !Ref BackendInflightGlobal" in globals_ and "INFLIGHT_STUDENT: !Ref BackendInflightPerStudent" in globals_""")

# ---------------------------------------------------------------- gateway: wait briefly for a free slot
sub("lambda/gateway.py", """    slots = [("inflight#global", setting("INFLIGHT_GLOBAL", 3)), (f"inflight#{slug}", setting("INFLIGHT_STUDENT", 2))]
    taken = []
    try:
        for key, cap in slots:
            if not acquire(key, cap, now):
                return reply(429, {"retry-after": "1"}, error="busy", message="The backend is busy. Please retry in a moment.")
            taken.append(key)
""", """    slots = [("inflight#global", setting("INFLIGHT_GLOBAL", 3)), (f"inflight#{slug}", setting("INFLIGHT_STUDENT", 2))]
    taken = []
    try:
        deadline = time.time() + setting("INFLIGHT_WAIT_MS", 0) / 1000
        for key, cap in slots:
            if not acquire_waiting(key, cap, now, deadline):
                return reply(429, {"retry-after": "1"}, error="busy", message="The backend is busy. Please retry in a moment.")
            taken.append(key)
""")
sub("lambda/gateway.py", "def acquire(key, cap, now):", '''def acquire_waiting(key, cap, now, deadline, sleep=time.sleep):
    """Take a slot, waiting (a few short sleeps with a little jitter) until `deadline` for one to free up. With no wait configured this is one try, as before.
    Only the platform's own slot counters wait here; the student's function has not been called yet, so nothing is run twice."""
    while True:
        if acquire(key, cap, now):
            return True
        if time.time() >= deadline:
            return False
        sleep(0.12 + random.random() * 0.1)


def acquire(key, cap, now):''')
sub("lambda/gateway.py", "import os\nimport re\nimport time\n", "import os\nimport random\nimport re\nimport time\n")
sub("lambda/gateway.py", "7 in-flight limiter (protects the rest of the AWS account)", "7 in-flight limiter (protects the rest of the AWS account; waits briefly for a free slot when INFLIGHT_WAIT_MS is set)")

# ---------------------------------------------------------------- the starter retries a polite number of times on "busy"
sub("starter/template/frontend/src/api.ts", """export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, 'network', 'Could not reach the server. Check your connection.')
  }
  const text = await response.text()
  let body: any = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    const code = String(body?.error ?? 'error')
    throw new ApiError(response.status, code, FRIENDLY[code] ?? String(body?.message ?? `Something went wrong (${response.status}).`))
  }
  return body as T
}""", """// "busy" and "too_many_requests" are answered by the platform BEFORE your backend runs, so asking again is always safe. A class using the AI at the same
// moment can hit them; waiting a moment and asking again (up to three times, longer each time, with a little randomness) is what a polite client does.
const RETRY_CODES = new Set(['busy', 'too_many_requests'])
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    let response: Response
    try {
      response = await fetch(`/api${path}`, {
        ...init,
        headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      })
    } catch {
      throw new ApiError(0, 'network', 'Could not reach the server. Check your connection.')
    }
    const text = await response.text()
    let body: any = null
    try {
      body = text ? JSON.parse(text) : null
    } catch {
      body = null
    }
    if (!response.ok) {
      const code = String(body?.error ?? 'error')
      if (response.status === 429 && RETRY_CODES.has(code) && attempt < 3) {
        const asked = Number(response.headers.get('retry-after')) * 1000
        await sleep(Math.min(Number.isFinite(asked) && asked > 0 ? asked : 400 * 2 ** attempt, 4000) + Math.random() * 300)
        continue
      }
      throw new ApiError(response.status, code, FRIENDLY[code] ?? String(body?.message ?? `Something went wrong (${response.status}).`))
    }
    return body as T
  }
}""")
print("ok")
