p = r"D:\Github_Codes\class-hosting-platform-github\template.yaml"
s = open(p, encoding="utf8").read()
a = '''            - !If # traces: once a month, when traces are about to expire, find the oldest one (a query on the day index) and send one alert email
              - UseTraces
              - Effect: Allow
                Action: dynamodb:Query
                Resource: !Sub '${TracesTable.Arn}/index/by-day'
              - !Ref AWS::NoValue
'''
assert a in s
b = a + '''            - !If # traces: the daily numbers (answers, refusals, errors, cost) are written as small items under their own prefix and read back
              - UseTraces
              - Effect: Allow
                Action: [dynamodb:GetItem, dynamodb:PutItem]
                Resource: !GetAtt TracesTable.Arn
                Condition:
                  ForAllValues:StringLike:
                    dynamodb:LeadingKeys: ['r#*']
              - !Ref AWS::NoValue
'''
s = s.replace(a, b, 1)
open(p, "w", encoding="utf8").write(s)

p = r"D:\Github_Codes\class-hosting-platform-github\tests\test_template_traces.py"
t = open(p, encoding="utf8").read()
t = t.replace('assert template.count("TracesTable.Arn") == 5, "two writers, the portal (table and indexes) and the daily sweeper (one index, one query): no one else is given the table"',
              'assert template.count("TracesTable.Arn") == 6, "two writers, the portal (table and indexes) and the daily sweeper (one index query, the r# summaries): no one else is given the table"', 1)
t = t.replace('assert "ALERTS_TOPIC_ARN', '''assert re.search(r"- UseTraces\\n\\s+- Effect: Allow\\n\\s+Action: \\[dynamodb:GetItem, dynamodb:PutItem\\]\\n\\s+Resource: !GetAtt TracesTable\\.Arn\\n\\s+Condition:\\n\\s+ForAllValues:StringLike:\\n\\s+dynamodb:LeadingKeys: \\['r#\\*'\\]", sweeper), "the sweeper writes only the r# summaries"
assert "ALERTS_TOPIC_ARN''', 1)
open(p, "w", encoding="utf8").write(t)
print("ok")
