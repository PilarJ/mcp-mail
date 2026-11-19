# mcp-mail PR 14: requested-head completion and current-head regression QA

Requested revision: `acb569bc9b182c3679f4deaf9300c507eb1e1dff`.
Additional tested revisions: `4b4a04b825667bc65237267563f2b99fa494fbbb` and current `12c554f5abe89e30980d5399e1e28329ee3942de`.

## Verdict and revision boundaries

The requested acb569b revision remains NOT-OK for the two P2 defects in [the original QA review](https://github.com/PilarJ/mcp-mail/pull/14#pullrequestreview-5976717712): reordered PPTX slides were read in archive-name order, and a PDF page rotated upright by /Rotate returned a sideways scan. That review is not withdrawn for its tested revision.

Both defects are fixed on 4b4a04b and remain fixed on 12c554f. All eight requested acceptance areas are complete; functional QA is PASS at 12c554f. This is functional approval only; security review and release authorization remain separate. No merge, deployment, live mailbox access or SMTP send was performed.

## Acceptance matrix

| Requested check | Execution and result |
|---|---|
| Every supported text format, Czech diacritics and multipage PDF | Current head: 48/48 independent MCP checks on real TLS Dovecot. DOCX, XLSX, PPTX, ODT, ODS, CSV (UTF-8 and Windows-1250), HTML, EML, ISDOC, XML, JSON, TXT and three-page text PDF preserve the Czech sentence. PDF page order and labels pass. |
| Scanned PDF and image as image content | Ordinary PDF scan, PNG and EXIF-6 JPEG pass through actual MCP tools. Rotated scanner PDF returns upright 600x900 instead of old sideways 900x600; independently rendered control and returned picture inspected visually. |
| Original base64 bytes | All 19 attachments in the format matrix, including an EML carried as a binary attachment, reproduce identical bytes. Corrected Czech PDF separately passes; original regression matrix also checks eight selected files. |
| Long text cap | A 425,000-byte UTF-8 text file with 320,000 characters is cut to exactly 200,000 characters; explicit truncated flag, exact full length and MAX_ATTACHMENT_TEXT_CHARS note. A separately configured 97-character cap returns exactly 97 with the notice. |
| mail_list and mail_search attachments and size | Both report true, all 19 exact filenames and the complete synthetic MIME size. The independent regression matrix also verifies thread summaries, false for no attachments, header/BODYSTRUCTURE-only fetches and no read-state mutation. |
| Corrupt or unsupported file | Broken PDF and unsupported ZIP return readable:false and a plain explanation/base64 fallback. A valid attachment is successfully read in the same MCP session after each failure. The broken PDF's expected upstream EOF warning does not stop the server. |
| Docker builds and starts | Unmodified Dockerfile builds for acb569b, 4b4a04b and 12c554f. Each starts the actual mail-mcp entrypoint and returns the designed root 404, protected MCP 401 and discovery 200. All 19 independent file-parser checks also pass in each installed image, with versions/source paths recorded. |
| Complete unmodified suite | Existing requested-head evidence: 715 passed in 32.24s. Independently executed follow-up: 729 passed on 4b4a04b in 39.74s; 756 passed on 12c554f in 35.37s. Each exits 0 with one upstream Authlib deprecation warning. |

Current-head additional matrix: 42/42 original real-IMAP/MCP regression checks, 4/4 Czech-PDF controls, dedicated rotated-scan reproduction, 19/19 installed-image checks and 3/3 HTTP-startup checks. HTML delta: 1.4–1.6 MB unclosed script/style/tag strings finish in 0.025–0.043 seconds locally, and ordinary Czech HTML retains its text while complete script/style blocks are removed (4/4 checks). These timings are a local functional regression probe, not a security certification or production load test.

## Environment and reproduction

macOS ARM64, Python 3.12.16, Docker; dedicated Alpine 3.20/Dovecot 2.3.21.1 with local TLS certificate and dummy-only authentication, bound to loopback 19924. No existing service was stopped. MCP checks use FastMCP Client with real application tools and real imaplib/imap-tools against that server. Source paths in JSON establish which checkout each invocation imports. Installed-container parsing and HTTP checks additionally verify the application image and its entrypoint.

The requested-head 715-test suite and original defects were already completed in the earlier routine and were read back rather than replayed. The new format matrix and Docker build were executed on acb569b to fill the requested coverage gaps. At 4b4a04b the complete suite and original regressions were rerun. When 12c554f changed HTML handling, the suite, full attachment matrix, original regressions and Docker build were repeated on that exact revision before final publication.

`run-suite.sh`, `run-build.sh`, `run-probes.sh`, `run-czech-pdf.sh`, `run-latest.sh`, `build-latest.sh`, `run-latest-container.sh` and the referenced Python files retain exact commands and assertions. The complete-suite command is the requested `uv run --python 3.12 --with pytest --with pytest-asyncio python -m pytest -q`. The stored current source checkouts remain clean; generated uv lockfiles are retained as QA artifacts outside the repositories. The supplementary interpreter has standard fixture writers (ReportLab, python-docx, python-pptx, openpyxl, PyMuPDF); extraction itself uses only application dependencies. No test source was changed.

## Helper failures and corrected evidence

The first supplementary Czech PDF used ReportLab's Vera font, which lacks five required glyphs (ě, ň, ř, ť, ů). Consequently acb569b and 4b4a04b initially recorded 47/48 format checks. Font coverage and independently extracted text confirmed this was an invalid test fixture. Those original logs/JSON are preserved. A three-page PDF with a complete bundled Unicode font was then independently written/read by PyMuPDF and passed 4/4 actual MCP checks on both revisions; the valid supplementary assertions total 51/51 per revision (47 initial valid checks plus four corrected-PDF controls). Only that invalid check is excluded. The 12c554f full format matrix uses the corrected PDF from the outset and passes 48/48; its additional PDF control also passes 4/4. No failed product assertion was relabelled as passing.

An early optional README lookup was wrong: the previous evidence folder has REPORT.md rather than README.md. The report and full execution/publication records were read successfully afterward. There was no failed runtime setup or denied operation in this follow-up. Original failed-helper and original application-defect evidence remain distinct.

## Scope and cleanup

Only self-created files/messages on the owned Dovecot fixture were read. External customer accounts, original business documents, physical-device photos, actual OAuth sign-in and SMTP sending were not exercised; the assignment explicitly excludes real mailboxes. EML byte preservation was checked for an EML stored as a binary file; message/rfc822 nested-message content is separately covered by the original matrix, without claiming MIME reconstruction byte identity. HEIC and legacy binary DOC/XLS/PPT are outside the declared supported formats and are not silently counted as tested.

No frontend changed, so browser layout, responsive screenshots, axe and Lighthouse do not apply. Returned scan and Czech-PDF pictures were inspected directly. Shared project-wiki reading is unavailable in the current tool catalog; the earlier failed unsupported CLI lookup was not repeated. The repository README, issue #13, current PR and existing tests establish scope; no repository AGENTS.md or QA skill exists in these checkouts.

Cleanup verifies removal of exactly four owned containers and their three anonymous application volumes. All three source checkouts are clean. `execution-audit.json` reconciles every claimed run with its result and records the corrected helper failure. The artifact manifest is produced after final test/cleanup logs stop changing and is independently checked before publication; it proves consistency, not test correctness.
