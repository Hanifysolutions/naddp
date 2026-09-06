# `storage/` — Local Object Store (demo only)

This directory is the NADDP demo's **object store**. In production this seam is an S3/GCS bucket
(server-side encryption, lifecycle policy, per-object ACLs). For the Ambassador demo it is a plain
local directory so that `make dev`, `make seed`, and `make demo-reset` work on a laptop with no
cloud credentials and no network dependency.

The seam is deliberate: `documents.object_uri` (and any future binary-bearing table) stores a
**URI string**, never bytes. Swapping this directory for a real bucket in Week 5+ is a change to one
resolver function and the URI scheme — no schema migration, no API change.

---

## `object_uri` convention

```
file://storage/<bounded_context>/<ulid>/<filename>
```

| Segment | Meaning |
|---|---|
| `file://` | Scheme. Signals "local demo object store". Production uses `s3://` or `gs://` with the same path shape after the bucket name. |
| `storage` | Fixed root, relative to the repository root. Never an absolute host path — absolute paths are not portable between the developer laptop, CI, and the Railway container. |
| `<bounded_context>` | One of `intelligence`, `opportunities`, `stakeholders`, `meetings`, `consular`, `diaspora`, `knowledge`, `governance`. Keeps ownership legible and makes a context-scoped purge (e.g. `demo-reset`) a directory delete. |
| `<ulid>` | The ULID primary key of the owning row (`documents.id`). One directory per document; time-sortable, so the store browses in ingestion order. See ADR-0007. |
| `<filename>` | The original, sanitised filename. Kept for human legibility in the trace drawer and download headers. Never used for lookup — the ULID directory is the identity. |

Example:

```
file://storage/intelligence/01K4Q7M2X8YB3F6N0R5T9WJHVD/austrade-lithium-value-chain-note.pdf
```

Resolution rules:

- Readers **must** resolve `file://storage/...` against the repository root, then verify the resolved
  path is still inside `storage/` before opening it. A `..` segment in a stored URI is a path-traversal
  attempt and must raise, not be normalised away silently.
- A missing object is an error to surface, not to swallow. The demo's rule is that every citation and
  every evidence item resolves; a dangling `object_uri` is a seed bug and must fail loudly in `make seed`.
- Classification lives on the **row**, not the file. Nothing in this directory is self-protecting —
  authorisation is decided in `app/security` before a URI is ever resolved (see ADR-0006).

---

## What may never be placed here

This directory holds **synthetic demo material only**. It is inside the repository, it has no access
control, and it is trivially readable by anyone with the checkout.

Never place here:

- Real citizen or consular data of any kind — no passport numbers, no visa records, no case
  correspondence, no next-of-kin details, no photographs of real people.
- Real personal contact details: private email addresses, phone numbers, home addresses.
- Anything obtained from a real government system, or any document marked with a real
  protective marking.
- Credentials, API keys, certificates, or `.env` files.
- Copyrighted source PDFs harvested from third parties. Intelligence signals cite **URLs**
  (`data/demo-seed/citations.json`); they do not mirror the source content.

Everything here is fabricated for the demo and is labelled SYNTHETIC in the UI. If a file would be
embarrassing or damaging on a public GitHub mirror, it does not belong in this repository at all.

---

## Lifecycle

- `make seed` writes the synthetic document set under `storage/<context>/<ulid>/`.
- `make demo-reset` truncates the database **and** empties this directory back to `.gitkeep`.
- `.gitignore` ignores everything here except `.gitkeep` and this README, so generated demo objects
  are never committed.
