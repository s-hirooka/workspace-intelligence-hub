# Portfolio Edition Publication Review

## Decision

The Portfolio Edition is designed for public release as a new repository with a single clean root commit. It does not inherit the private repository history.

## Publication scope

Included material is limited to application source, tests, database migrations, synthetic samples, generic configuration templates, and the offline source-analysis helper. Customer documents, generated reports, deliverables, invoices, production databases, media, backups, runtime logs, local environment files, credentials, and model weights are excluded.

## Review controls

The release review checks the complete publication tree and its new Git history for:

- known credential formats and private-key markers
- non-empty secret assignments
- email addresses, phone numbers, postal codes, and personal-name fields
- customer and project identifiers from the private edition
- absolute local paths and non-example network endpoints
- accidentally tracked ignored files
- large or binary artifacts outside the allowlist

Synthetic values used by security tests are assembled or clearly reserved for examples so they cannot be mistaken for live credentials.

## Review results

- Public tree: 108 tracked files, 624,507 bytes
- Largest tracked file: 95,526 bytes
- Gitleaks 8.29.1: no leaks found
- Forbidden tracked paths: 0
- Private customer or project identifiers: 0 matching files
- User-specific absolute paths: 0 matching files
- Automated tests: 153 passed
- Offline Roslyn analyzer: build completed with 0 warnings and 0 errors

The personal-information pattern check found 10 email, 5 phone, and 4 postal-code fixtures, all under automated tests. The email addresses use reserved example domains, and the phone and postal values are synthetic inputs required to verify the application's PII detector and import filters. No operational person or customer record is included.

## Runtime verification

The automated test suite passes from the Portfolio Edition tree. External API tests use fakes and reserved `example` domains. Live API calls remain disabled without local credentials.

## Residual risk

Automated scanners cannot prove the absence of every sensitive fact. Contributors must continue to review new files, keep operational data outside the repository, and rotate any credential immediately if it is ever committed.
