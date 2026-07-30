# Idios evidence commitment format, version 1

Status: format specification. The delivery manifest convention in section 3 is usable today with no contract change. The requester evidence commitment in sections 4 to 6 specifies a field that does not exist in the live v2 contract; it is the agreed shape for a future contract revision and nothing here changes what is deployed.

This document is the versioned format referenced by the evidence commitment design settled in July 2026: one commitment per side, the worker's carried by the existing `delivery_hash`, the requester's added at dispute filing, no rebuttal phase, and the contract never reading or interpreting any of it.

## 1. What a commitment establishes, and what it does not

An evidence commitment fixes a party's evidence set at a known point in time and prevents later substitution: material assembled after the fact cannot be passed off as what existed when the commitment was written.

It establishes nothing else. In particular it does not establish availability (a party can commit and never reveal), sufficiency, truth, or any evidentiary verdict. From the contract's side a withheld reveal and an honest one are the same 32 bytes. Resolution states (`ResolvedToAlice`, `ResolvedToBob`) remain purely economic outcomes; nothing in any resolution path reads a commitment as an input. Arbitrators weigh revealed evidence against the commitments; the contract holds the commitments and does nothing with them.

Reveal is out of band. Evidence travels to the arbitrators over whatever channel the parties use (Beam wallet messaging, the listed dispute contact). The working norm is that committed evidence which is never revealed counts against the party who committed it. The contract cannot verify availability, so a norm is the ceiling here, and both stall paths already carry a cost: a worker who reveals nothing risks collateral and bond to an adverse vote, and a requester who files and goes silent risks losing, and forfeits the dispute fee to the treasury if the dispute voids on arbitrator timeout.

## 2. Hash function and encoding rules

Every hash in this format is SHA-256, matching `spec_hash`, `result_hash`, and `delivery_hash` in the contract. Every multi byte integer in a preimage is big endian. Every field is fixed width; there are no delimiters and no variable length fields, so no framing ambiguity exists. A 32 byte hash field whose value was never set is 32 zero bytes and is committed verbatim; committing to zeros is unambiguous, it commits to the fact that the field was not set.

## 3. Delivery manifest convention (live today, no contract change)

Mode B stores `delivery_hash` at submission without interpreting it. This convention defines the deliverable as a container that carries its own evidence, so the existing field doubles as the worker side commitment, timed before any dispute exists and therefore impossible to tailor to a complaint.

The delivery is a single container file (tar, tar.gz, or zip, as the parties agree). `delivery_hash` is the SHA-256 of that container's bytes, exactly as the worker already submits it. Recommended container layout:

    manifest.json          the delivery manifest, see below
    <deliverable files>    the work itself
    evidence/              optional supporting material (logs, inputs, receipts)

`manifest.json` fields, all required:

    {
      "format": "idios-delivery-manifest",
      "format_version": 1,
      "job_id": <the job id as a JSON number>,
      "spec_hash": "<64 hex chars, the job's spec_hash verbatim, all zeros if none>",
      "files": [ { "path": "<container path>", "sha256": "<64 hex chars>" } ]
    }

Every file in the container other than `manifest.json` itself appears in `files` with its hash. The `spec_hash` entry binds the manifest to the job's stored value, zeros included.

Limitation, stated plainly: nothing on chain records which convention a given job used. The worker writes `delivery_hash` alone and the creation call has no delivery format field, so an observer of the chain cannot tell a plain file hash from a manifest container hash. The convention binds only as far as the parties' agreement. Declaring it inside the specification that `spec_hash` commits to closes that for jobs that set a spec_hash; for jobs that leave it zero, the declaration has nowhere on chain to live and the agreement is out of band only. This is an accepted floor for a commitment the contract never interprets, not a solved problem.

## 4. Requester evidence commitment (future contract revision, not live)

One new 32 byte argument on the dispute call, written exactly once, atomically with filing, signed by the same requester key the dispute call already signs. Stored under a new per job storage namespace (the next free tag), never inside the `Job` struct. No new phase, no new window, no new deadline, and no change to the arbitrator timeout void path.

The field is required and must be nonzero: a dispute cannot be filed without it. This is a filing gate only. Rationale: a requester filing a dispute is asserting a factual complaint, and requiring the commitment at that moment means the evidence set must exist before the dispute does, which removes speculative disputes filed before any case has been assembled. The gate never touches resolution: given a dispute exists, no commitment value influences which outcome fires or what it pays (section 6).

The stored value is:

    commitment = SHA-256(preimage)

with the preimage defined in section 5.

## 5. The preimage

Fixed width, 142 bytes total, fields concatenated in this order:

    offset  size  field            encoding
    0       4     format_version   uint32 big endian, = 1 for this document
    4       32    contract_cid     the Idios contract id, raw bytes
    36      8     job_id           uint64 big endian, matching the contract's uint64 job_id
    44      1     phase            uint8: 2 = dispute (1 reserved for a future committed delivery form)
    45      1     role             uint8: 1 = requester (2 reserved for the worker side)
    46      32    spec_hash        the job's stored spec_hash verbatim, all zeros if none was set
    78      32    delivery_hash    the job's stored delivery_hash verbatim
    110     32    evidence_root    the root of the evidence bundle, section 6

A dispute can only be filed from `AwaitingApproval`, which requires a submitted delivery, so `delivery_hash` always exists by filing time and binding to it is always well defined.

The cid, job id, phase, and role are already implied by where and how the value is stored on chain (contract scoped storage, keyed by job id, written by the dispute method under the requester's signature). They are bound into the preimage anyway for portability: an evidence bundle and its commitment travel off chain during a dispute, and the domain separation makes a commitment from one job, one contract, one phase, or one role unusable as any other. Verifying a revealed bundle means rebuilding this preimage from the chain's stored values plus the bundle's root and checking the digest against the stored commitment.

## 6. The evidence bundle and evidence_root

In format version 1 the evidence bundle is a single container file (tar, tar.gz, or zip). `evidence_root` is the SHA-256 of the container's bytes. Recommended container layout:

    evidence_manifest.json    same shape as the delivery manifest, "format": "idios-evidence-bundle"
    <evidence files>          the material itself

A Merkle tree root with per file selective disclosure is deliberately deferred to a future format version; a flat container hash is sufficient for the commitment property in section 1 and keeps verification to one hash of one file.

## 7. Acceptance criteria for the contract change

The contract revision that adds the requester commitment is gated, like every Idios contract change, on the model fuzzer holding its invariants first. The two properties specific to this field, agreed as the definition of correct:

1. Immutability. The commitment record is written exactly once, at filing, and is byte identical under every subsequent call in every randomized call sequence. No method other than the dispute filing writes that key.
2. Zero influence on outcomes. Given a dispute exists, the commitment's value has no effect on any economic result: paired call sequences identical in everything except the commitment value produce identical statuses, identical fund flows, and identical terminal states. The one permitted effect of the field is the filing gate in section 4 (present and nonzero to file), which acts before the dispute exists and never after.

Together these keep the boundary this format exists to preserve: commitments fix timing, arbitration decides the contested economic outcome, collateral prices the consequence, and no resolution state ever becomes an evidence validity verdict.

## 8. Format versioning

This document defines format version 1. Any change to the preimage layout, the bundle definition, or the manifest fields is a new format version: the `format_version` integer moves in the preimage and in the manifest files together, and old commitments verify forever under the version they were written with.
