root = r"D:\Github_Codes\class-hosting-platform-github" + "\\"
def sub(p, a, b):
    f = root + p
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))

sub("docs\MCP.md", "| Tools | `init_project`, `check_project`, `deploy`, `status`, `logs`, `update_kit` |", "| Tools | `init_project`, `check_project`, `deploy`, `status`, `logs`, `project_status`, `pull_project`, `push_project`, `update_kit` |")
sub("docs\MCP.md", "| `update_kit` | Brings the kit's own files", """| `project_status` | Project sync: the student's projects (personal and team) and what is not sent yet. Reads only | No |
| `push_project` | Project sync: sends the files the student changed to their project on the platform (`dry_run=true` only lists). Never sends secrets, `node_modules`, build output or kit files. A file a teammate changed is not overwritten: the answer lists it | **Yes** (`confirm=true`; not needed for `dry_run`) |
| `pull_project` | Project sync: brings newer files from the platform into the folder (`dry_run=true` only lists). A file both sides changed is saved as `<file>.theirs`, never overwritten | **Yes** (same) |
| `update_kit` | Brings the kit's own files""")

sub("docs\PROJECT_SYNC.md", "## For the teacher", """## Moving existing users and existing work onto project sync

Nothing is lost and nothing changes for a student until they use the new commands. The steps, in order:

**Teacher (once)**
1. Update the platform: `plan`, show the plan, `apply <plan id>` (this release adds one permission, `projects/*`, and republishes the portal, the kit and the editor-tools package). Check `verify`.
2. Tell students that you can read the project files they push, and what they are for.
3. Optional: in the **Projects** tab make a team project for each group and add its members.

**A student who already has a project (kit folder)**
1. Open a terminal in the project and run any kit command, for example `npm run deploy` or `npm run project`. The kit **updates itself first** (old copies of changed kit files go to `.kit-backup/`), which adds `npm run push`, `npm run pull` and `npm run project`. Their own code is never touched.
2. Run `npm run push -- --dry-run` to see what would be sent, then `npm run push`. The whole existing project is now on the platform (about 50 files for the starter).
3. A big file or folder that should not be shared: add its name to `.syncignore` and push again.

**The same student on another computer**
1. Download the starter kit from the portal and unzip it, `npm run login` (the token is entered by the student, hidden), then `npm run pull`.
2. From then on: `npm run pull` when they sit down, `npm run push` when they finish.

**A team**
1. The teacher makes the team project and adds the members (**Projects** tab).
2. One member pushes the existing code: `npm run push -- --project <team-id>` (from their folder). The others: new folder from the kit, `npm run login`, `npm run pull -- --project <team-id>`.
3. Everyone then pulls before they start and pushes when they finish. If `push` reports a conflict, follow the message: `pull`, keep what you want, delete the `.theirs` copy, `push --mine`.

**Students who work through an AI editor (the tool server)**
- *Kit folder with the editor's own settings (VS Code `.vscode/mcp.json`, which starts `tools/mcp.mjs`):* the kit update in step 1 above also updates the tool server's file. **Restart the tool server (or reload the editor)** and the three tools `project_status`, `push_project` and `pull_project` appear. The AI asks before it pushes or pulls.
- *Package set up earlier with `npx -y <portal>/class-platform-mcp.tgz setup`:* the editor is pinned to an older version on purpose, so it keeps working but has no project tools. Ask the AI to run `status`: it says a newer tool package exists. Run `npx -y <portal>/class-platform-mcp.tgz setup` **once**, accept the replacement of the `class-platform` entry, and restart the tool server. The project must also have the new kit (step 1) because the tools run the project's own `portal-deploy.mjs`.
- Either way the student never gives the AI the token; `npm run login` stays the student's own step.

## For the teacher""")
sub("CHANGELOG.md", "No setting to switch on; it costs cents of storage.", "The editor tool server gets three tools, `project_status`, `push_project` and `pull_project` (the last two ask before they act). Existing users and their existing work: see \"Moving existing users and existing work onto project sync\" in `docs/PROJECT_SYNC.md`. No setting to switch on; it costs cents of storage.")
sub("starter\rules\AGENTS.md", "- **New computer:**", "- **If your editor has the `class-platform` tools:** use `project_status`, `push_project` and `pull_project` (ask the student first, then `confirm=true`; `dry_run=true` only looks). Otherwise run the npm commands.\n- **New computer:**")
print("ok")
