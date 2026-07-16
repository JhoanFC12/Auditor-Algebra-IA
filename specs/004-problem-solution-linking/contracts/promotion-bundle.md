# Contract: Problem-Solution Bundle Promotion

## Preflight

A bundle is promotable only when:

- its persisted review status is `human_confirmed`;
- staging preflight derives `promotion_status = ready_for_db`;
- its staging problem is ready and non-continuation;
- every included link is human-confirmed;
- every multipage solution is explicitly complete and has a coherent fragment
  sequence;
- all units share book, instance and exercise set;
- every crop exists and matches its registered digest when present;
- the bundle source digest is current;
- the persisted bundle is reloaded and its fingerprint is revalidated at the
  write boundary so a concurrently revoked review cannot be committed;
- the target database/profile passes the existing promotion policy.

If the instance opted into solution processing, a problem without a bundle is
blocked unless absence was confirmed globally or with the per-record terminal
decision `solutions_absent_confirmed`.

## Canonical payload

The problem payload includes `soluciones` as rich visual groups:

```json
[
  {
    "solution_group_id": "solution_7",
    "variant_index": 1,
    "images": ["managed/solutions/solution_7_1.png"],
    "fragments": [
      {
        "fragment_id": "fragment_7_1",
        "page_number": 150,
        "bbox_xyxy": [100, 200, 900, 1300],
        "fragment_role": "single",
        "crop_sha256": "..."
      }
    ],
    "source": {"exercise_set_id": "practice_04"},
    "link": {"status": "human_confirmed", "method": "exact_number"},
    "bundle_id": "psb_..."
  }
]
```

## Transaction

For each bundle independently:

1. insert or update the problem identity;
2. write the complete visual-solution payload;
3. upsert the factory origin/provenance;
4. commit;
5. update staging audit after the DB commit.

Any error before commit rolls back that bundle. Other bundles continue.
Solution crops are copied into managed storage before their managed paths are
persisted; staging absolute paths are retained only as audit provenance.

## Idempotence

- Existing problem identity uses the current book/instance/origin rules.
- Replaying the same bundle replaces the same visual-solution groups.
- Replacing a confirmed bundle removes visual groups previously managed by that
  bundle but no longer present, while preserving legacy and foreign groups.
- Duplicate solution-group IDs within a bundle are invalid.
- The report includes solution-group counts and bundle ID.
