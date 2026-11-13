root = r"D:\Github_Codes\class-hosting-platform-github"
def sub(p, a, b, count=1):
    f = root + "\\" + p.replace("/", "\\")
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, count))

P = "lambda/portal.py"
sub(P, "    def delete(self, pk):\n        self.table.delete_item(Key={\"pk\": pk})\n", '''    def delete(self, pk):
        self.table.delete_item(Key={"pk": pk})

    def replace_if(self, item, field, expected):
        """Replace the item only if its `field` still equals `expected` (optimistic concurrency). True when replaced."""
        from botocore.exceptions import ClientError
        try:
            self.table.put_item(Item=item, ConditionExpression="#f = :e", ExpressionAttributeNames={"#f": field}, ExpressionAttributeValues={":e": expected})
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True
''')
sub(P, "    def delete(self, pk):\n        self.items.pop(pk, None)\n", '''    def delete(self, pk):
        self.items.pop(pk, None)

    def replace_if(self, item, field, expected):
        with self.lock:
            current = self.items.get(item["pk"])
            if not current or current.get(field) != expected:
                return False
            self.items[item["pk"]] = json.loads(json.dumps(item))
            return True
''')
sub(P, '''    if path in ("/api/student/domain", "/api/student/domain/status") and method in ("PUT", "DELETE", "GET"):''', '''    if path == "/api/student/projects" or path.startswith("/api/student/projects/"):
        import projects
        routed = projects.student_route(student_token(event, now), method, path, body, now)
        if routed is None:
            return reply(404, error="Unknown route.")
        return reply(routed[0], **routed[1])
    if path in ("/api/student/domain", "/api/student/domain/status") and method in ("PUT", "DELETE", "GET"):''')
sub(P, '''        if path == "/api/admin/worker" and method == "GET":''', '''        if path == "/api/admin/projects" or path.startswith("/api/admin/projects/"):
            import projects
            routed = projects.admin_route(method, path, body, now)
            if routed is None:
                return reply(404, error="Unknown route.")
            return reply(routed[0], **routed[1])
        if path == "/api/admin/worker" and method == "GET":''')
sub(P, "  GET|POST|DELETE /api/student/knowledge[/collections|/{shelf}/documents[/{id}[/start]]]   the student's own book shelf for the kit's folder sync (knowledge.py student_route)\n", "  GET|POST|DELETE /api/student/knowledge[/collections|/{shelf}/documents[/{id}[/start]]]   the student's own book shelf for the kit's folder sync (knowledge.py student_route)\n  GET /api/student/projects, GET /{id}, POST /{id}/download|prepare|commit   project sync: source files shared through the bucket (projects.py)\n  GET|POST /api/admin/projects, GET|POST|DELETE /api/admin/projects/{p|t}/{name}[/file|/restore|/compact]   the teacher's projects\n")
