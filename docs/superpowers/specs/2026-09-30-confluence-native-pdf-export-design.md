# Confluence Native PDF Export Design

## Goal

Export Confluence pages as PDFs using Confluence Cloud's own PDF exporter. The exported files should preserve Confluence's server-side rendering, including page styling and supported page content, without requiring a local PDF renderer or browser.

## Confirmed scope

- PDF becomes the default format in the web console and CLI.
- Markdown and AsciiDoc remain available as explicit output formats.
- Each Confluence page produces its own file named from the sanitized page title, with a `.pdf` extension.
- Child pages retain the existing output directory hierarchy.
- Existing-file skip and overwrite behavior applies to `.pdf` files.
- Existing attachment download option remains independent of PDF generation.
- Page totals, per-page completion, cancellation, and diagnostic logs continue to work.

## Export flow

The Rust Confluence client will implement the Cloud PDF export flow using the same site URL and Basic authentication credentials already used for the Confluence API:

1. Request `/wiki/spaces/flyingpdf/pdfpageexport.action?pageId={page_id}` with the `X-Atlassian-Token: no-check` header.
2. Parse the returned export task ID from the response HTML.
3. Poll `/wiki/api/v2/pdfexporttask/progress/{task_id}` every three seconds until the task succeeds, fails, or reaches a ten-minute export deadline. Each HTTP request also respects the existing request timeout. Polling must be asynchronous and must observe task cancellation.
4. Resolve the task result URL against the Confluence site when it is relative, then download the result using the configured bandwidth limiter.
5. Verify the body begins with the PDF signature `%PDF-` before writing it to the target path.

The task endpoint and export action are Confluence Cloud implementation endpoints, not a documented public PDF REST API. Keep this integration isolated in the Confluence client so a future Atlassian endpoint change has a narrow impact.

## Application behavior

- Add `pdf` to `OutputFormat`; set PDF as the default while preserving explicit `markdown` and `asciidoc` choices.
- Add a format selector to the web form, defaulting to PDF, and pass the selected format to the CLI process.
- Route PDF pages through the native export method instead of converting storage content into Markdown/AsciiDoc.
- Skip a page before starting PDF export when its `.pdf` already exists and overwrite is disabled.
- For page trees, export each page to one PDF at its existing title-derived path. The page tree's current total and per-page events remain the source of UI page progress.
- When attachment downloading is enabled, continue downloading attachments as separate files using existing directory and skip/overwrite behavior. The Confluence PDF exporter remains responsible for the content rendered inside each PDF.
- Emit clear errors for non-success HTTP responses, missing task IDs, failed tasks, timeout/cancellation, invalid result URLs, and non-PDF response bodies. Do not write a partial or invalid PDF as a completed page.
- Keep PDF export task polling separate from the existing per-request timeout; a page export may take longer than one HTTP request.

## Authentication and security

- Reuse the existing email/API-token Basic authentication; do not read browser cookies or ask the user to paste session cookies.
- Do not include credentials, authorization headers, or signed result URLs in UI events or diagnostic logs.
- Follow result URLs only when they resolve to the configured Confluence host; reject unexpected hosts to avoid forwarding credentials outside the site.

## Testing and acceptance

- Unit-test export response task-ID parsing, task progress success/failure/timeout, relative and absolute result URL handling, PDF signature validation, and host validation using mocked HTTP responses.
- Test format extension and CLI defaults/overrides, web form default/selection argument propagation, page-tree PDF filenames, skip-existing behavior, and progress event accounting.
- Run the repository-supported Rust test runner (`cargo nextest run`) for affected tests, the Python web test suite, and a release build.
- Acceptance: selecting PDF in the console and running the user's real Confluence page creates readable `.pdf` files from Confluence's export service, one per page, preserves the tree directories, and reports completed/skipped counts correctly. A mocked test run cannot confirm permissions or endpoint behavior for a particular Atlassian tenant.

## Risks and limitations

- Atlassian does not document a supported Cloud REST endpoint for PDF export; implementation endpoints may change without notice.
- PDF export includes only the published version and applies Confluence's own export rules and permissions. Draft-only changes may not appear.
- Existing local Markdown content will not be converted into PDF; PDF output is generated from the corresponding published Confluence page.
- Very large pages may exceed the ten-minute export deadline; report that limit clearly so the user can retry or export smaller page sets.
