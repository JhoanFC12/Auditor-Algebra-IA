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

## Decision 8: Alternatives use one or more contiguous answer blocks

**Decision**: The parent `problem` region always contains every alternative needed by the problem. `answer_block` is a child region: use one box when the alternatives form one continuous visual block and multiple boxes, all linked to the same problem, when the layout is genuinely discontinuous.

**Rationale**: One box per alternative would multiply labels and weaken the existing detector contract; one mandatory envelope can absorb unrelated content in multi-column or figure-heavy layouts. Contiguous blocks preserve both completeness and geometric precision.

**Alternatives considered**:

- One envelope for all alternatives: rejected as a universal rule because separated columns can force excessive foreign content.
- One box per option: rejected for the current contract because the target class is an answer block, not an option detector.

## Decision 9: Precision is semantic coverage plus class-specific exclusion

**Decision**: Define versioned inclusion and exclusion profiles for `problem`, `problem_number`, `answer_block` and `solution`. Required content must be complete; recurring headers/footers, page numbers, advertising, scan artifacts and neighboring units are excluded unless a documented semantic exception applies.

**Rationale**: Non-degenerate coordinates and valid crop hashes do not prove a useful box. The observed false continuation of a solution into a repeated header demonstrates that geometry requires semantic purity and explicit negative checks.

**Alternatives considered**:

- Validate only IoU: rejected because a high-IoU box may still contain a neighboring option or recurring header.
- Use fixed top/bottom page bands as hard exclusions: rejected because valid mathematical content can legitimately reach those zones; band checks remain warnings that require semantic evidence.

## Decision 10: Multipage continuity requires positive evidence

**Decision**: A `continues_on`/`continues_from` relation requires evidence from both fragment boundaries, compatible unit identity and an unfinished semantic or editorial continuation. Repeated furniture, blank strips and page numbers are explicitly negative evidence.

**Rationale**: Proximity to a page edge alone produced false multipage units. Positive evidence makes continuation auditable and allows abstention when either boundary is uncertain.

## Decision 11: Agent output becomes a relational annotation dataset

**Decision**: Preserve supervised outputs as versioned region and unit annotations plus relations `contains`, `belongs_to`, `continues_on`, `continues_from`, `solves`, `has_answer_block`, `precedes` and `same_entity`.

**Rationale**: Flat boxes cannot train or evaluate reconstruction of split problems, multiple answer blocks or problem-solution relationships. A relational layer remains model-agnostic and can be projected into YOLO or other task-specific datasets.

**Alternatives considered**:

- Store only rendered overlays: rejected because geometry, identity and relations cannot be reconstructed reliably.
- Extend the current three-class YOLO file as the canonical schema: rejected because it cannot represent solutions, multipage identity or graph relations without sidecar data.

## Decision 12: Evaluation splits are document-level

**Decision**: Assign each complete document to exactly one of train, validation, test or difficult/out-of-distribution sets. Derivatives inherit the document split.

**Rationale**: Random page splits leak editorial templates, typography and adjacent content from the same book, overstating generalization.

**Alternatives considered**:

- Random page split: rejected due to leakage.
- Split by instance while sharing one source PDF: rejected unless document-level isolation can still be proven.

## Decision 13: Specialized capability boundaries remain architecture-agnostic

**Decision**: Plan at least two autonomous capabilities: structural document analysis and mathematical segmentation/linking. A capability may be implemented by one model or an orchestrated set of specialized models, but its input/output contract and evaluation remain stable.

**Rationale**: Prematurely forcing one model per capability would mix architecture choice with the business contract. Stable contracts allow experimentation while preventing permanent dependency on Gottfried or Ingrid.

## Decision 14: Promotion requires metrics, critical-error audit, abstention and rollback

**Decision**: A candidate model cannot replace the supervised route until it passes the IND-MA-01 thresholds on unseen documents, has no systematic critical error in an evaluated family, supports abstention, receives human approval and can be rolled back.

**Rationale**: Aggregate scores can hide systematic failures such as omitted alternatives or headers classified as solutions. Operational independence must be reversible and risk-aware.

## Decision 15: Pre-H-PS1 maps require immutable visual sessions

**Decision**: Materialize one read-only Problem Detector Lab session per exact
`map_id + map_revision`. The adapter revalidates the live map hash and all
scope/page/P/S/R references, then renders full pages, coarse regions and each
problem-solution relation side by side. Browser marks remain session-only.

**Rationale**: Textual reports are insufficient to audit editorial numbering,
shared coarse regions and relation evidence. A separate manifest makes the
visual evidence reproducible without mutating the r0 map or confusing an
inspection mark with H-PS1 approval.

**Alternatives considered**:

- Reuse the post-H-PS1 Ingrid activation bundle: rejected because it would
  require an approval that this review is intended to decide.
- Auto-discover every map as implicitly approved for review: rejected because
  materialization, validation and revision history must be explicit.
- Persist review buttons through canonical app routes: rejected because H-PS1
  remains a separate human authority event.

## Resolved Scope

- Included: page roles, precise problem/alternative/solution annotation contracts, relational ground truth, document-level split policy, persisted solution units, deterministic proposals, review, confirmed bundles, model-evaluation gates and atomic visual-solution promotion.
- Excluded from this planning increment: selecting the final model architecture, executing training, automatic canonical approval, remote production deployment, OCR/normalization of solution text and semantic method classification.
