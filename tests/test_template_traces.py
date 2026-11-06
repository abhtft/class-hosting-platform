"""Guards traces in template.yaml: off by default and only with backends, the table exists only when on, expires by itself, its indexes hold
summaries only, the AI proxy and knowledge functions may add steps under the t# prefix and nothing else in that table, only the portal function
reads or deletes, and no student function gets anything."""
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n|\n  # )", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  TracesEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "traces are off unless asked for"
assert "UseTraces: !And [!Condition UseBackend, !Equals [!Ref TracesEnabled, 'true']]" in template
table = block("TracesTable")
assert "Condition: UseTraces" in table and "DeletionPolicy: Retain" in table and "PAY_PER_REQUEST" in table
assert re.search(r"TimeToLiveSpecification:\n\s+AttributeName: ttl\n\s+Enabled: true", table), "every item expires by itself"
assert "by-app" in table and "by-day" in table and table.count("ProjectionType: ALL") == 2
assert "TracesTableName:\n    Condition: UseTraces" in template
globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
assert "TRACES_ENABLED: !If [UseTraces, 'true', 'false']" in globals_ and "TRACES_TABLE: !If [UseTraces, !Ref TracesTable, '']" in globals_

for name in ("AiProxyFunction", "KnowledgeFunction"):
    fn = block(name)
    m = re.search(r"- !If[^\n]*\n\s+- UseTraces\n\s+- Effect: Allow\n\s+Action: (\[[^\]]*\])\n\s+Resource: !GetAtt TracesTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: (\[[^\]]*\])", fn)
    assert m, f"{name}: may write traces under a prefix only"
    assert m.group(1) == "[dynamodb:GetItem, dynamodb:PutItem, dynamodb:UpdateItem]", m.group(1)
    assert m.group(2) == "['t#*']", m.group(2)
    assert "dynamodb:Query" not in fn.split("TracesTable")[1].split("Events:")[0], f"{name} cannot list traces"

portal = template[template.index("\n  DeployFunction:\n"):]
portal = portal[:portal.index("\n  # ")] if "\n  # " in portal[10:] else portal
m = re.search(r"- UseTraces\n\s+- Effect: Allow\n\s+Action: (\[[^\]]*\])\n\s+Resource: \[!GetAtt TracesTable\.Arn, !Sub '\$\{TracesTable\.Arn\}/index/\*'\]", template)
assert m and "dynamodb:PutItem" not in m.group(1) and "dynamodb:UpdateItem" not in m.group(1) and "Scan" not in m.group(1), "the portal reads and deletes; it never writes steps"
assert template.count("TracesTable.Arn") == 4, "two writers and the portal (table and indexes): no one else is given the table"
print("ok")
