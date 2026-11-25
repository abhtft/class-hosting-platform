# Knowledge tab: books in Studio (2026-11-26)

**Problem.** Books (the PDFs and text an agent answers from) could only be added with the teacher's command; Studio showed only shelf names. The owner could not see, add or remove what an agent knows.

**Built.** A **Knowledge** tab: add a PDF, text, Markdown or HTML file to a shelf (or a new shelf); it is read in the background; open a book to see its passages; ask the shelves a question (passages, scores, sure or not); remove a book or a shelf; "Use in this agent" edits the draft's shelves.

**Parts.**
- `ChatwootBooksFunction` (new, 2048 MB, 10 minutes): the **only writer** of the shelves. It reads an uploaded file (`knowledge_ingest`), embeds it, stores it in `knowledge/tenant/<tenant>/<shelf>/`, updates the index, counts the cost against the business's month, deletes the uploaded file. Also removes a book or a shelf, builds the list from existing shelves (`reindex`) and returns a book's first passages.
- The **index** is one item `books#<tenant>` in the adapter's table (shelves, books, pending ones). The admin function reads and writes only that item and signs upload forms (`chatwoot-books/`), so its permissions stay small.
- Routes: `GET /admin/books`, `POST /admin/books/refresh|upload-url|search`, `POST /admin/books/{id}/start`, `GET /admin/books/{shelf}/{doc}/passages`, `DELETE /admin/books/{shelf}/{doc}|shelves/{shelf}|pending/{id}`. The question search is a worker call (`books_search`): only the question is embedded, no model.

**Rules.** Books are **not versioned**: adding or removing a book changes what every agent reading that shelf knows at once. A shelf read by an agent's **live** version cannot be removed. Limits (settings): 40 books, 10 shelves, 10 MB a file (`knowledge.maxFileMB`), the existing page and passage limits. A same-content file is not read or paid twice; a changed file with the same name replaces the older book.

**Not built.** Scanned PDFs (no text recognition), per-agent shelf ownership (shelves belong to the business), purge of orphan files.
