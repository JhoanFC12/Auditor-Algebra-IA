# Research: Enlace Problema-Solucion

## Decision 1: Two page selections in the existing instance snapshot

**Decision**: Keep `page_selection` and top-level `selected_pages` as the problem-page contract. Add `solution_page_selection`, `structure_mode` and `solution_status` as sibling fields.

**Rationale**: Existing instances, APIs and detection jobs already consume `selected_pages`. A sibling selection permits overlap and avoids an SQL migration because instance configuration is already persisted as structured metadata.

**Alternatives considered**:

- Replace the existing selection with a generic page-role map: cleaner eventually, but it would break legacy consumers and require a larger migration.
- Create separate solution instances: rejected because problem and solution belong to one logical practice and must be promoted together.

## Decision 2: Rule-based linker with explicit evidence

**Decision**: Introduce a pure linker that scopes every match by book, instance and exercise set, then scores exact numbering, confirmed section mapping, reading order and proximity according to the editorial pattern.

**Rationale**: The signals are inspectable, deterministic and easy to correct. The first version needs auditability more than an opaque learned matcher.

**Alternatives considered**:

- Match only by number: rejected because numbering resets and can be duplicated.
- Match only by order: rejected because omissions create silent shifts.
- Train a relationship model now: rejected because no reviewed link bank exists yet.

## Decision 3: Human confirmation remains mandatory

**Decision**: Even high-confidence proposals stay non-canonical until a human confirms them. High scores only support batch review.

**Rationale**: A wrong relationship can make a mathematically valid solution appear attached to the wrong problem. The user already established human confirmation as the final gate.

**Alternatives considered**:

- Auto-promote exact number matches: deferred until a reviewed benchmark proves acceptable false-link risk.

## Decision 4: Persist link work under staging

**Decision**: Store solution units, candidate links and bundles in an atomic, versioned workspace under the instance staging root. Attach the confirmed bundle to the staging problem record before promotion.

**Rationale**: It reuses the Factory's review-first lifecycle, survives browser refresh and prevents partial canonical writes.

**Alternatives considered**:

- Insert candidates into canonical DB tables: rejected because proposals are not truth.
- Keep candidates only in browser state: rejected because refresh or reconnect would lose decisions.

## Decision 5: Store visual solutions before semantic solutions

**Decision**: Promote confirmed visual solution groups as rich entries in the problem's solution payload, including images and provenance. The existing normalized solution table remains reserved for later OCR/LaTeX and method data.

**Rationale**: The current semantic table requires normalized mathematical content, while this feature starts with reviewed boxes and crops. Rich visual groups satisfy immediate traceability without inventing solution text.

**Alternatives considered**:

- Make semantic fields nullable and insert empty solution rows: rejected because it weakens the meaning of the normalized table.
- Store only image paths: rejected because page, box, fragment and link evidence would be lost.

## Decision 6: Per-bundle atomic and idempotent promotion

**Decision**: Extend existing per-record promotion so the problem, visual-solution payload and origin update commit together. Repeated promotion updates the same problem identity rather than appending solution duplicates.

**Rationale**: The current loop already isolates commits by staging problem. Extending the same transaction preserves unaffected records when one package fails.

**Alternatives considered**:

- One transaction for a whole instance: rejected because a single bad bundle would block all independent work.
- Separate problem and solution uploads: rejected because it permits partial official state.

## Decision 7: Separate box and link corrections

**Decision**: A box correction continues feeding detector training data; a changed relationship creates a linker-review event in its own history.

**Rationale**: Geometry errors and relationship errors have different causes and future learning targets.

## Resolved Scope

- Included: page roles, persisted solution units, deterministic proposals, review, confirmed bundles, atomic visual-solution promotion and tests.
- Excluded: training a solution detector, OCR/normalization of solution text, automatic canonical approval, remote production deployment and semantic method classification.
