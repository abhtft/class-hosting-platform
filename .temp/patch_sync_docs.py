root = r"D:\Github_Codes\class-hosting-platform-github" + "\\"


def sub(p, a, b):
    f = root + p.replace("/", "\\")
    s = open(f, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(f, "w", encoding="utf8").write(s.replace(a, b, 1))


# backup message
sub("installer/data.mjs", "the published files, the class knowledge (books and the class library) and the teacher login", "the published files, the class knowledge (books and the class library), the project source files and the teacher login")

# student rules (a kit file: the kit version is already 2.3.2)
sub("starter/rules/AGENTS.md", "## Commands the student (or you) can run", """## Keeping the project's files on the platform (project sync)

The student's source files can live on the platform too, so the same code is on every computer they use and a team can share one project **without GitHub accounts**. It is not Git: whole files, and it stops when two people changed the same file.

- `npm run push` sends the files that are new, changed or deleted here. `npm run pull` brings the changes made elsewhere. `npm run project` lists the student's projects and what is not sent yet. `--dry-run` shows the plan and changes nothing.
- Every student has a **personal** project (`personal`, the default). The teacher can make **team projects**; `npm run pull -- --project robot-club` (and `push`) works on one, and the folder remembers it.
- **New computer:** download the starter kit from the portal, `npm run login`, then `npm run pull` (the kit's own files come from the portal, the project's files from the platform).
- **Conflict:** if two people changed the same file, `push` stops and says who changed it; nothing is overwritten. `npm run pull` saves their copy next to the student's as `<file>.theirs`. The student keeps what they want in their file, deletes the `.theirs` copy, and runs `npm run push -- --mine`. Or `npm run pull -- --theirs` takes the platform's copy (the student's is kept as `<file>.mine`). Never delete a `.theirs` or `.mine` file for the student without asking.
- **What is never synced:** the kit's own files, `node_modules`, `.git`, build output, `.env` files, keys, the `knowledge/` books, and anything listed in `.syncignore` (one name, `folder/` or `*.ext` per line). A file can be up to 5 MB, a project 50 MB and 500 files. If a push is refused for size, add the big files to `.syncignore`; do not split secrets into files to get them synced.
- Do not edit `.class-project.json` (it records what was last synced on this computer). Run `npm run push` when the student finishes a piece of work and `npm run pull` when they sit down at a computer they used before; both are quick.

## Commands the student (or you) can run""")
sub("starter/rules/AGENTS.md", "| `npm run deploy` | Publishes the backend, then the frontend, straight from this computer. |", "| `npm run push` / `npm run pull` / `npm run project` | Project sync: send this folder's source files to the platform, bring changes made elsewhere (another computer or a teammate), or list the projects. See \"Keeping the project's files on the platform\". |\n| `npm run deploy` | Publishes the backend, then the frontend, straight from this computer. |")

# the design and reference page
open(root + "docs\\PROJECT_SYNC.md", "w", encoding="utf8").write("""# Project sync

Students keep the **source files** of their project on the platform, so the same code is on every computer they use and a team can share one project **without GitHub accounts**. It is not Git: it moves whole files and stops when two people changed the same file.

## For a student

```bash
npm run push                       # send the files you changed (new, changed, deleted)
npm run pull                       # get what changed elsewhere (another computer, a teammate)
npm run project                    # your projects and what is not sent yet
npm run pull -- --project robot-club   # work on a team project (remembered for the folder)
```

- **New computer:** download the starter kit from the portal, `npm run login`, `npm run pull`. The kit's own files come from the portal; the project's files from the platform.
- **Conflict:** `push` stops and says who changed the file; nothing is overwritten. `pull` saves their copy as `<file>.theirs`. Keep what you want in your file, delete the `.theirs` copy, then `npm run push -- --mine`. Or `npm run pull -- --theirs` takes the platform's copy (yours is kept as `<file>.mine`).
- **Never synced:** the kit's files, `node_modules`, `.git`, build output, `.env` files, keys, the `knowledge/` books, and what you list in `.syncignore` (one name, `folder/` or `*.ext` per line). Limits: 5 MB a file, 50 MB a project, 500 files.

## For the teacher

The **Projects** tab lists every project (personal and team), the files with who last changed them, and the history (who, when, which files). You can **make a team project and choose its members**, open any file, **put an earlier version of a file back**, **freeze** a project (nothing can be pushed), **clean up old versions** and **delete** a project. A student who leaves a team can no longer pull or push; their earlier changes stay. Nothing needs switching on, and nothing runs or costs while idle. The files are in the platform's own bucket (`projects/`), and the cost is the storage (cents). The teacher can read students' source files: tell students.

## How it works

- **Storage.** A file is stored by its SHA-256 (`projects/<p|t>/<name>/blobs/<sha256>`): uploaded once, never overwritten. One item in the tokens table is the project's **manifest** (path to sha256, size, author, time) with a **revision** number; one small history item per commit (kept 180 days).
- **Push** is two calls. `prepare` checks the changes against the project as it is now and returns S3 upload forms for the files S3 does not have yet (S3 itself enforces the size and the SHA-256). `commit` checks that the files arrived and replaces the manifest **only if nobody committed in between** (a conditional write on the revision). Every change says which version of the file it started from (`base`); if somebody else changed that path since, the whole push is refused with `409 conflict` and the list of paths and who changed them. Changes to other paths never conflict, even from an older revision. A project that keeps changing under a push answers "busy" after three tries.
- **Pull** lists the manifest and downloads what differs through short-lived links; the client checks every file's SHA-256 before writing it. A conflicting file is never overwritten.
- **Safety.** Paths are relative, without `..`, backslashes or control characters, and never `.env*`, keys, `.git` or `node_modules` (the server refuses them too). A student reaches only their personal project and the teams they belong to; the portal never reads a file's bytes (it signs links); S3 access is the portal function only (`projects/*`), not CloudFront.
- **Limits.** 500 files, 5 MB a file, 50 MB live per project, 150 MB of stored versions in total (old versions stay until the teacher cleans up), 100 paths a push.
- **Backups.** `export` copies `projects/` and the manifests (they are in the tokens table).

## Decisions and what is left out (version 2.3.2)

- A personal project is made on the first push; a team project only by the teacher. One token per person (no shared token), so history shows who changed what.
- No line-by-line merging (whole files only), no branches, no per-file locks, no deleting a student's personal project when the student is removed (delete it in the Projects tab).
- Not synced on purpose: the kit's files (they follow the teacher's portal), `platform.json`, secrets.

## Routes

Student (login session): `GET /api/student/projects`; `GET /api/student/projects/{id}` (`personal` or a team id); `POST .../{id}/download` `{paths}`; `POST .../{id}/prepare` and `POST .../{id}/commit` `{changes: [{path, op: put|delete, sha256, size, base}]}`.
Teacher: `GET|POST /api/admin/projects`; `GET|POST|DELETE /api/admin/projects/{p|t}/{name}`; `POST .../file` `{path}` (a download link), `.../restore` `{path, rev}`, `.../compact`. See [API_REFERENCE.md](API_REFERENCE.md) section 7.
""")

# API reference
sub("docs/API_REFERENCE.md", "### 7.3 Calls made by a student's function", """Project sync (see [PROJECT_SYNC.md](PROJECT_SYNC.md)):

| Route | Answer |
|---|---|
| `GET /api/student/projects` | The student's projects: `personal` and the teams they belong to (`id`, `kind`, `title`, `members`, `rev`, `files`, `bytes`, `frozen`) |
| `GET /api/student/projects/{id}` | The manifest: `files` as path to `{sha256, size, by, ts}`; `404` for a project the student is not in |
| `POST /api/student/projects/{id}/download` | `{paths}` (up to 100) to short-lived download links with the expected `sha256` |
| `POST /api/student/projects/{id}/prepare` | `{changes: [{path, op: "put"\\|"delete", sha256, size, base}]}` to S3 upload forms for the files S3 lacks (size and SHA-256 enforced by S3); `409 conflict` with `conflicts` (path, who changed it) when a path changed since `base`; `413` over a limit; `403` frozen |
| `POST /api/student/projects/{id}/commit` | The same body: applies it only if nobody committed meanwhile; `{rev}`; `409 conflict`, or `409` when an upload is missing or the project is busy |

Teacher: `GET|POST /api/admin/projects` (list; make a team project `{id, title, members}`), `GET|POST|DELETE /api/admin/projects/{p|t}/{name}` (files and history; change `title`, `members`, `frozen`; delete), `POST .../file` `{path}` (download link), `POST .../restore` `{path, rev}` (puts a file back as a new revision), `POST .../compact` (deletes versions no longer shown).

### 7.3 Calls made by a student's function""")

# teacher guide
sub("docs/TEACHER_SETUP.md", "| **Custom domain** | their own domain for their site | see section 7 |", "| **Project sync** | their source files on the platform: the same code on every computer, and **team projects** you make (the **Projects** tab: members, files, history, put a file back, freeze, delete). Not Git: whole files, and a file two people changed is never overwritten silently. Always available, nothing to switch on, cents of storage. You can read students' source files; tell them. See [PROJECT_SYNC.md](PROJECT_SYNC.md) | nothing extra |\n| **Custom domain** | their own domain for their site | see section 7 |")
sub("docs/TEACHER_SETUP.md", "It does **not** contain traces", "It also contains the **project source files** (personal and team projects, with their history). It does **not** contain traces")

# architecture
sub("docs/TEACHER_ARCHITECTURE.md", "| S3 `sites/`, `archive/`, `userdata/`, `knowledge/` | published sites, archived sites, student files, class knowledge (books, the class library and their indexes) | yes (`portal/` is republished; `uploads/` is temporary) |", "| S3 `sites/`, `archive/`, `userdata/`, `knowledge/`, `projects/` | published sites, archived sites, student files, class knowledge (books, the class library and their indexes), project source files (stored by content; the manifests are in the tokens table) | yes (`portal/` is republished; `uploads/` is temporary) |")

# docs map, README, student README, changelog
sub("docs/README.md", "| [COMPUTE_WORKER.md](COMPUTE_WORKER.md)", "| [PROJECT_SYNC.md](PROJECT_SYNC.md) | Project sync: source files shared through the platform (second computer, teams) without GitHub accounts; how it works, limits, routes |\n| [COMPUTE_WORKER.md](COMPUTE_WORKER.md)")
sub("README.md", "An **optional compute worker**", "**Project sync:** students keep their source files on the platform (`npm run push`, `npm run pull`), so the same code is on every computer they use, and the teacher can make **team projects**, all without GitHub accounts: [docs/PROJECT_SYNC.md](docs/PROJECT_SYNC.md).\n\nAn **optional compute worker**")
sub("student/README.md", "- **Features you may have, each switched on by your teacher:**", "- **Your files on every computer (and teams):** `npm run push` sends your source files to the platform, `npm run pull` brings changes made on another computer or by a teammate, `npm run project` shows your projects. Your teacher can make a team project for a group. If two people change the same file, nothing is overwritten: `push` stops and `pull` saves their copy as `<file>.theirs`. Secrets, `node_modules` and build output are never sent.\n- **Features you may have, each switched on by your teacher:**")
sub("CHANGELOG.md", "## 2.3.1\n", """- **Project sync: your source files on every computer, and team projects, without GitHub accounts.** Students now have `npm run push` and `npm run pull` (and `npm run project`). A student's project is kept in the platform's own bucket, so the same code is on a second computer after `npm run login` and `npm run pull`. The teacher can make **team projects** (the new **Projects** tab: choose members, open any file, see who changed what and when, put an earlier version of a file back, freeze, clean up old versions, delete). It is not Git: whole files move, and when two people changed the same file `push` stops and says who changed it, nothing is overwritten, and `pull` saves their copy as `<file>.theirs` (`push --mine` and `pull --theirs` are the two ways out; `--dry-run` shows a plan). Secrets, `node_modules`, build output, the kit's own files and the `knowledge/` books are never synced; a file can be 5 MB, a project 50 MB and 500 files. Files are stored by their SHA-256 and checked by S3 on upload and by the client on download; the portal never reads a file. Backups (`export`, `import`, `transfer`) include `projects/`. New: `lambda/projects.py`, routes under `/api/student/projects` and `/api/admin/projects`, one new bucket permission (`projects/*`, the portal function only), `tools/project.mjs` in the kit (kit 2.3.2). No setting to switch on; it costs cents of storage. Details: `docs/PROJECT_SYNC.md`. **To apply:** `plan` and `apply <plan id>` (one permission); the kit sync brings the commands to student projects.

## 2.3.1
""")
print("ok")
