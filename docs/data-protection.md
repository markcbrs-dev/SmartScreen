# Data Protection Design

This project now uses application-layer encryption for uploaded resumes and sensitive applicant data.

## What Is Encrypted

- Uploaded resume files stored under `media/resumes/`
- Applicant PII fields: first name, last name, contact number, email
- Parsed resume-derived fields: raw resume text, parsed skills, parsed education, parsed experience, parsed locations
- Match notes stored for applicants

Job descriptions remain plaintext in this design so HR can continue reviewing and debugging them without adding operational complexity.

## Minimal Deployment Design

1. Keep Django as the only application component.
2. Use one application-layer encryption key exposed as `SMARTSCREEN_DATA_KEY`.
3. If `SMARTSCREEN_DATA_KEY` is not set, derive encryption from `SECRET_KEY` so local development and simple deployments still work.
4. Continue storing files on the local filesystem and records in the existing database.
5. Keep HTTPS, secure cookies, and protected resume downloads enabled.

This avoids introducing KMS, object storage, or envelope-encryption services while still protecting data at rest from direct database and disk reads.

## Operational Guidance

1. In production, set `SMARTSCREEN_DATA_KEY` separately from `SECRET_KEY`.
2. Back up the encryption key securely. Losing it means encrypted resumes and applicant data cannot be recovered.
3. Restrict direct filesystem and database access to trusted operators only.
4. Rotate the key with an explicit re-encryption maintenance step rather than changing it in place.
5. Keep the protected `/resumes/<filename>` route as the only supported resume download path.

## Tradeoffs

- Exact database filtering on encrypted applicant PII is intentionally limited.
- Resume files are decrypted in application memory on read, which is acceptable for the current 5 MB upload cap.
- Historical applicant records are encrypted too, which protects audit snapshots but makes raw SQL inspection less convenient.