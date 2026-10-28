# ICE collector text-extraction diagnostics

This describes the source collector, not deployment or publication approval.

## Transport is not evidence

`FetchResult.ok`, fetch-receipt `ok`/`status`, and provider `success` retain their
existing transport/index-collection meanings. A successful HTTP response is not
proof of readable supporting text, validated findings, or publication readiness.
Collection-health calculation and operational-receipt schemas are unchanged.

Fetch receipts add `content_type`, `text_extraction_status`, and
`text_extraction_error`. Statuses distinguish `not_attempted`, `not_assessed`,
`unsupported`, `no_supporting_passage`, and `supporting_passage_extracted`.
Article exclusions also supply `qualification_reason` on the fetch receipt.
An extracted passage remains subject to the existing manual editorial review.

## Unsupported PDFs

The project does not declare a supported PDF parser. Requested/final PDF URLs,
PDF media types, and PDF magic bytes therefore fail closed with
`pdf_text_extraction_unsupported`. The collector does not decode PDF objects,
compressed streams, or document metadata into purported supporting prose, and
does not use the link title as a substitute. HTTP success does not trigger a
fallback request merely because PDF extraction is unsupported.

The discovery remains in collection exclusions with its source/canonical URL,
index, title, publisher, retrieval time, HTTP status, official listing date where
available, `NEEDS_REVIEW`, and `supporting_evidence_missing`. It is not labeled
false or promoted into a normalized event. Existing saved records and queues are
not rewritten by this source fix. A listing date can establish the report's
listed issue date but cannot establish its findings.

## HTML evidence

The ICE-only extractor omits headings, semantic navigation, hidden attributes,
and link-only blocks; it does not fall back to a title. It prefers event-bearing
body passages over agency background. Missing prose is retained as
`no_supporting_passage`; body text without an event signal is retained as
`no_event_level_ice_signal` for review.

This is conservative text extraction, not a browser render or semantic fact
validator. Fully linked prose may require manual review. CSS-only visibility,
unmarked boilerplate, and inaccessible content still require source review.
The helper's text/PDF-date routines do not themselves validate evidence.

## Offline verification and remaining gates

`tests/test_ice_text_extraction.py` builds valid synthetic compressed,
uncompressed, and textless PDF fixtures using only the standard library. Tests
mock both transport paths and check retained discovery/receipt fields, title and
navigation exclusions, and preserved ordinary HTML evidence. Run the ICE suite
with an explicit admitted `DISPATCHES_TEST_ROOT` and unique child `--basetemp`.

No PDF parser was added. Enabling real PDF extraction would require a separately
supported dependency and text-quality/error tests. Windows PowerShell execution,
protected-source integration, runtime installation/proof, and any historical
record remediation remain separate gates; offline tests do not establish them.
