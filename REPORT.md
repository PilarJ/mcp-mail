# mcp-mail PR 14 — QA FAILED

Tested current head: `acb569bc9b182c3679f4deaf9300c507eb1e1dff`.
Initial head: `7266733eb8047f78b0ab97a6532dab67f79f1873`.
Main control: `bf8fe4d67c7bff846dec410a1e5f21d9091b701e`.
Scope: issue 13, PR description and current security-fix delta. Only synthetic local mail and disposable fixtures were used. No production mailbox, customer data, SMTP send, application-source change, merge or deployment.

## Executed results

| Scenario | Result |
|---|---|
| Original unmodified suite on 7266733 | 711 passed, 1 upstream Authlib deprecation warning, 33.50 s, exit 0 |
| Current unmodified suite on acb569b | 715 passed, same upstream warning, 32.24 s, exit 0 |
| Independent in-process MCP Client, real imaplib/imap-tools, real TLS Dovecot 2.3.21.1 | 40/42 checks passed; exit 1 for the two defects below |
| List, raw search, thread summaries | All three list 10 exact attachment names (including forwarded-message child), true attachment status and the complete 181,810-byte MIME message size; no full body downloaded |
| Empty attachment list | Correct false status and empty names |
| Body truncation and RFC822 size | Exact total length 327, explicit truncated flag; same message size as Dovecot |
| ReportLab PDF invoice, openpyxl XLSX, python-docx document | Identifier, total, both sheet names, values, paragraphs and table preserved |
| EXIF orientation 6 JPEG | Correctly converted from 900x600 stored pixels to 600x900 upright output; independently checked red/blue pixel positions |
| Ordinary scan, forwarded EML and Czech filename | Image output, inner body and nested attachment name, and UTF-8 name preserved |
| Raw mode | Eight selected attachments byte-identical; forwarded EML readable content tested, raw MIME reconstruction not asserted |
| Read state | All messages remain unread after listing, search, thread, body and every attachment read; no UID STORE; all attachment fetches use PEEK |
| Main control | Same local mail reports false attachments and 243 bytes on main; invoice still returns original base64 only. Confirms the intended regression scope |
| Focused scanned-PDF orientation reproduction | Actual MCP response is sideways 900x600; independent PDF rendering is upright 600x900; exit 1 |

Environment: macOS ARM64, Python 3.12.16, dependency resolution in a local venv; tested source imports are recorded in the independent JSON. Dovecot runs in a dedicated Alpine 3.20 Docker container with TLS verification and its public test certificate trusted explicitly. Its test-only static authentication accepts any dummy password; no actual credential was used. Port 19914 is bound to loopback only. The initial full-suite venv remains the dependency environment; `PYTHONPATH` pins the current and baseline source trees separately.

## P2 — PPTX extraction ignores presentation order

Location: `src/mail_mcp/attachments.py:447-452`.

1. Open/create a two-slide presentation, with titles `Original first` and `Moved to first`.
2. Move the second slide before the first and save. The included `fixtures/reordered.pptx` was generated with python-pptx and reopened independently; the recorded presentation order is `Moved to first`, then `Original first`.
3. Attach it to a mail and call `mail_get_attachment` without a format override.
4. Expected: `## Slide 1` is `Moved to first`, then `## Slide 2` is `Original first`.
5. Actual: `## Slide 1` is `Original first`, then `## Slide 2` is `Moved to first`. The code sorts ZIP member filenames instead of following the presentation's slide list and relationships. This gives the agent an incorrect document sequence.

Evidence: `independent-results.json` (expected titles and actual response), `fixtures/reordered.pptx`, `check-independent.py`. Reproduced through the actual tool on both reviewed heads, not just by calling an internal parser.

## P2 — scanned PDF loses the page's orientation

Location: `src/mail_mcp/attachments.py:275-276`, with `_largest_image` at 285-305.

1. Use `fixtures/scan-upright-with-rotation.pdf`. Like a rotated scanner output, its raw embedded image is sideways at 900x600; the valid PDF page `/Rotate=90` makes the document upright at 600x900.
2. Open it normally: `QA SCAN 014` and `TOTAL 300 CZK` are upright. `scan-expected-render.png` is an independent PyMuPDF page rendering.
3. Attach the file and call `mail_get_attachment` in default readable mode.
4. Expected: an upright page image with its page rotation applied.
5. Actual: `readable:true`, `image:true`, but the returned 900x600 picture is on its side (`scan-actual-returned.png`). The extractor returns the raw XObject image and never applies the PDF page rotation or placement transform.

The ordinary portrait scan and phone EXIF rotation both pass in the same environment, isolating this to PDF orientation. `scan-rotation-results.json`, the PDF, both PNGs, actual response metadata, the saved runner and log reproduce the defect. Both PNGs were visually inspected.

## Coverage limits and excluded helper failure

A real Dovecot server was exercised with application-generated synthetic MIME messages and standard file writers. Genuine business invoices, original physical-phone photographs, actual customer mailboxes and externally hosted Dovecot were not read; the requested real mailbox credentials (`IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASS`) and representative original files are unavailable in this unattended run. Available Pujcovna credentials are unrelated and were not used. SMTP/OAuth deployment and the application Docker build were not retested. The new four resource-cap tests passed as part of the current suite; this is not a substitute for the separate security review. This PR has no web UI, so responsive page layout, Lighthouse and browser axe checks are not applicable; its returned scan images were checked directly.

The first helper expected only the outer message text in `body_chars`, but imap-tools also combines the attached text/plain part. Its observed total was retained in the original-head evidence; the corrected expectation derives from the independent full parsed MIME text, and the current check passes. This is a test-helper assumption, not an application defect. A final optional absent-file read caused one batched shell result to return exit 1; subsequent guarded reads verified its already-completed clone and dependency installation. No denied path was retried.

Project wiki lookup failed (`dodaiosctl wiki recall "moduly-prestashop mcp-mail"`, tool exit 2); no repository QA skill or AGENTS.md exists in this clone. The project register, repository README, issue criteria, PR and tests establish the scope. All findings belong to this open PR; no separate issue.

## Reproduction and integrity

`run.sh`: execute the current unmodified suite and independent tool checks with source pinned through PYTHONPATH.
`run-scan.sh`: create the upright rotated-scan fixture, independently render it, read the attachment through the current MCP tool, and compare dimensions.
`check-baseline.py`: same Dovecot messages through the pinned main source.
The local Dovecot Dockerfile/configuration are included. Start a new disposable instance as described by the saved setup commands; the helpers create only their named synthetic folders. Dependencies and actual versions are in `package-versions.txt`.

The local container is removed after evidence collection. The proof images and fixtures contain only QA text. SHA256SUMS is generated after the final evidence copy; independent checksum verification establishes file consistency, not application correctness. Verdict: FAILED; qa-ok withheld. The owner should obtain fixes in this same PR and request re-QA.
