# Debug Investigation: file.io CLI Breakage (LimeWire Migration)

## Observations

- Reproduction command:
  - `/Users/tsca/testdir/file.io-cli-tddschn/.venv/bin/file.io-cli /Users/tsca/Downloads/console.csv`
- Reproduced behavior (unsandboxed network):
  - Progress reaches 100%
  - CLI exits with code 1
  - Error: `Upload API returned non-JSON response (HTTP 200); url=https://www.file.io/; content-type=text/html; redirect-chain=https://file.io/ -> https://www.file.io/; ...`
- Environment:
  - OS: macOS (arm64)
  - Python: 3.14.4
  - requests: 2.29.0
- Current CLI implementation still posts multipart body to `https://file.io` and expects JSON containing `link`.
- HAR analysis (`refs/browser-upload-and-redirect.har`) shows browser upload is no longer old `file.io` JSON API:
  - Primary host: `api.limewire.com` (42/46 requests)
  - Upload sequence includes:
    - `GET /crossdomain`
    - `POST /sharing/user-encryption-key`
    - `GET /users/self/passphrase`
    - `POST /sharing/upload/s3/multipart`
    - `GET /sharing/upload/s3/multipart/{uploadId}/{part}`
    - `POST /sharing/upload/s3/multipart/{uploadId}/complete`
    - `POST /sharing/upload`
  - Additional step after redirect context:
    - `POST /sharing/claim-buckets-external`
- HAR analysis (`refs/limewire-generate-public-sharing-url.har`) for “generate share URL” button shows:
  - `POST /sharing/bucket/{id}/enable-sharing`
  - `POST /sharing/replication/SHARING_BUCKET/push?clientDeviceId=...`
- HAR request headers indicate token/csrf mechanics:
  - file.io-origin flow uses `x-claim-token`
  - limewire.com-origin flow uses `x-csrf-token`
- HAR response bodies are mostly omitted (size present, `content.text` absent), so exact response JSON fields are partially unknown from HAR alone.

## What Works vs Fails

- Works:
  - `GET https://api.limewire.com/crossdomain` returns JSON with `claimToken`.
  - `POST https://api.limewire.com/sharing/user-encryption-key` succeeds with `x-claim-token`.
  - `GET https://api.limewire.com/users/self/passphrase` succeeds *after* `user-encryption-key` call in same session + claim token.
- Fails:
  - Legacy `POST https://file.io` returns HTML from `https://www.file.io/` (not JSON).
  - Direct `POST /sharing/upload/s3/multipart` with empty/minimal payload returns validation errors demanding many structured fields (`sharingBucket.*`, `contentItem.*`, `fileEncryptionKey.*`).

## Minimal Reproduction

- Minimal failing behavior is now a single `POST` to `https://file.io` expecting JSON:
  - API contract changed; endpoint now redirects to web app and returns HTML.
- Minimal proof old endpoint is unusable for CLI API:
  - `requests.post('https://file.io', files=...)` => 301 to `https://www.file.io/` then HTTP 200 HTML.

## Hypotheses

### H1: Root cause is full API migration from simple file.io upload endpoint to LimeWire multi-step encrypted upload flow (ROOT HYPOTHESIS)
- Supports:
  - Browser HAR uses only `api.limewire.com` for upload + bucket operations.
  - Legacy endpoint returns HTML, not JSON.
  - Validation from `/sharing/upload/s3/multipart` requires complex objects (not legacy multipart upload).
- Conflicts:
  - None observed.
- Test:
  - Verify key LimeWire endpoints are callable from Python and require structured encrypted payloads.

### H2: Failure is only missing browser-like headers (Origin/Referer), old endpoint still works with correct headers
- Supports:
  - Browser requests include Origin/Referer.
- Conflicts:
  - Direct test with Origin/Referer still returns HTML after redirect.
- Test:
  - `POST https://file.io` with multipart + browser-like headers.

### H3: Failure is only claim-token/csrf handling, not upload API structure
- Supports:
  - Browser includes `x-claim-token` and `x-csrf-token` in different phases.
- Conflicts:
  - `/sharing/upload/s3/multipart` rejects even authenticated call when payload shape is insufficient.
- Test:
  - Acquire claim token + create user key + call multipart endpoint with minimal payload and inspect validation.

### H4: Public-share URL generation requires authenticated cookie-bound session and cannot be fully anonymous
- Supports:
  - User reports file visible with proper cookies but not incognito.
  - Public URL generation is separate button/API (`enable-sharing` + replication push).
- Conflicts:
  - Legacy product behavior was anonymous public links; migration may preserve anonymous capability through claim flow.
- Test:
  - Attempt anonymous end-to-end flow with claim token only and inspect whether API allows enable-sharing without browser cookie session.

## Experiments

### E1 (H2): Legacy endpoint with browser-like headers
- Change:
  - One-off script using `requests.post('https://file.io', files=..., headers={Origin,Referer})`
- Result:
  - HTTP 301 -> `https://www.file.io/`, then HTTP 200 HTML.
- Conclusion:
  - H2 rejected.

### E2 (H1): Probe crossdomain + claim-token bootstrap
- Change:
  - One-off script: `GET /crossdomain`, then `POST /sharing/user-encryption-key`
- Result:
  - `crossdomain` 200 with claimToken
  - `user-encryption-key` 200 JSON
- Conclusion:
  - Supports H1.

### E3 (H1/H3): Passphrase endpoint ordering dependency
- Change:
  - Call `GET /users/self/passphrase` with claim token before and after `POST /sharing/user-encryption-key`
- Result:
  - Before key creation: 404 JSON error
  - After key creation: 200 JSON with passphrase
- Conclusion:
  - Supports H1 and endpoint sequencing requirements.

### E4 (H1/H3): Multipart creation with insufficient payload
- Change:
  - Call `POST /sharing/upload/s3/multipart` with `{}` and with empty nested objects
- Result:
  - 400 validation_error requiring many fields:
    - `sharingBucket.id/name/sharingStatus/sharingPermission/primaryEncryptionKeyId/createdDate/deleted`
    - `contentItem.id/originalSharingBucketId/s3Status/itemType/mediaType/size/baseFileEncryptionKeyId/ephemeralPublicKey/createdDate/deleted`
    - `fileEncryptionKey.id/publicKey/createdDate/deleted`
- Conclusion:
  - H3 rejected as sole cause; structure + crypto workflow required.

### E5 (H1): End-to-end API prototype with synthetic key material
- Change:
  - Implemented a one-off Python prototype that:
    - bootstraps claim token and user key,
    - creates multipart upload,
    - uploads part to signed URL,
    - completes upload and finalizes content item.
- Result:
  - All steps returned 200 and produced `sharingBucketId` + `contentItemId`.
- Conclusion:
  - Confirms a non-browser Python flow is feasible.

### E6 (H4): Enable-sharing with extracted CSRF token
- Change:
  - Added `PUT /crossdomain/csrf` before `POST /sharing/bucket/{id}/enable-sharing`.
- Result:
  - `enable-sharing` returns 200 with bucket object and `sharingId`.
- Conclusion:
  - CSRF extraction is required for this phase; direct JWT-embedded CSRF was rejected.

### E7 (Shareability): Cross-session claimability check
- Change:
  - Fresh session used emitted `claimToken` to bootstrap key + csrf and call `POST /sharing/claim-buckets-external`.
- Result:
  - `{"amountBucketsClaimed":1}`
- Conclusion:
  - Claim-token URL is usable across sessions (not tied to uploader cookie state).

## Root Cause

- The CLI still used legacy `POST https://file.io` and expected JSON, but file.io now routes uploads through a LimeWire multi-step API workflow on `api.limewire.com`.

## Fix

- Replaced legacy upload path in `file_io_cli_tddschn/cli.py` with a new flow:
  - `GET /crossdomain` for claim token bootstrap
  - `POST /sharing/user-encryption-key`
  - `POST /sharing/upload/s3/multipart`
  - `GET /sharing/upload/s3/multipart/{uploadId}/1`
  - signed URL part upload
  - `POST /sharing/upload/s3/multipart/{uploadId}/complete`
  - `POST /sharing/upload`
- Emitted share link format is now:
  - `https://limewire.com/?claimToken=...&sharingBucketId=...`
- Added expires parsing for relative values (`1w`, `7d`, etc.) and ISO timestamps.
- Added input materialization support so file/stdin/tar modes can all be uploaded through the new API (API requires known size).

## Verification

- Exact user command now succeeds and prints a link:
  - `/Users/tsca/testdir/file.io-cli-tddschn/.venv/bin/file.io-cli /Users/tsca/Downloads/console.csv`
- Cross-session claim test succeeds for emitted link data:
  - Fresh session `POST /sharing/claim-buckets-external` returns `{"amountBucketsClaimed":1}`.
