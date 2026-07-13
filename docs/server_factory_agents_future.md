# Server Factory Agents Future Scope

This document records the autonomous-agent roadmap without making agents a
dependency for the current server-side PDF Factory implementation.

## Current Scope Boundary

The current `002-server-factory-models` feature must work with direct user
actions and server jobs only:

- PDF/page selection is user-driven.
- Problem segmentation runs as an observable server job.
- Local segmentation models run from configured server model paths.
- OCR remains on the Hugging Face endpoint.
- Human review remains the source of truth for corrections.
- Corrections are saved as training data.

No current acceptance path requires an autonomous agent.

## Deferred Agent Ideas

### Book Organizer Agent

Future responsibility:

- classify uploaded books by course;
- propose instances;
- detect theory, solved problems, and proposed problems;
- propose useful page ranges.

Required before implementation:

- reliable server-side library metadata;
- safe permissions model;
- audit log of every proposed change;
- human approval before modifying books or instances.

### Problem Segmentation Review Agent

Future responsibility:

- inspect model-detected page boxes;
- flag likely bad boxes;
- propose corrected problem, number, and answer-block boxes;
- add high-confidence corrections to the golden base after human approval.

Required before implementation:

- stable multiclass detector output;
- clear page/box correction schema;
- metrics for false positives, missing boxes, and reading-order errors.

### OCR Review Agent

Future responsibility:

- compare crop image with raw OCR;
- flag likely OCR rule violations;
- propose corrected raw OCR;
- feed only accepted corrections into the OCR training bank.

Required before implementation:

- OCR rule auditor;
- batch review UI;
- training-bank quality gates.

### Golden Base Curator Agent

Future responsibility:

- select useful corrected examples;
- detect duplicates;
- maintain train/validation splits;
- prepare retraining candidates.

Required before implementation:

- immutable correction history;
- dataset manifest checks;
- per-model evaluation metrics.

## Explicitly Out Of Scope Now

- automatic creation of books or instances;
- automatic promotion to `problemas`;
- autonomous OCR correction without human approval;
- autonomous database edits;
- autonomous retraining or model promotion.

## Implementation Rule

Agents may propose work in a later feature, but every write operation must keep
the same safety rule as the current Factory: human approval first, staging before
database, and training data preserved with provenance.
