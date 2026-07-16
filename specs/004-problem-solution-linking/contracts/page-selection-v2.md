# Contract: Instance Page Selection V2

## Request

The existing instance page-selection operation accepts:

```json
{
  "db_name": "math_db",
  "book_id": 10,
  "page_count": 200,
  "selected_pages": [20, 21, 22],
  "solution_selected_pages": [22, 150, 151],
  "structure_mode": "hybrid",
  "solution_status": "identified",
  "exercise_set_id": "practice_04",
  "source": "web_ui"
}
```

Rules:

- `selected_pages` remains the problem-page selection.
- `solution_selected_pages` is optional; omission preserves the prior solution selection.
- Both arrays are deduplicated, sorted and range-checked.
- Overlap is valid.
- Unknown enum values return a validation error without mutation.
- Legacy requests preserve all V2 fields.

## Persisted Snapshot

```json
{
  "page_selection": {
    "schema_version": "library_instance_page_selection_v1",
    "selected_pages": [20, 21, 22],
    "page_ranges": [{"start_page": 20, "end_page": 22}],
    "review_status": "pending"
  },
  "solution_page_selection": {
    "schema_version": "library_instance_solution_page_selection_v1",
    "selected_pages": [22, 150, 151],
    "page_ranges": [
      {"start_page": 22, "end_page": 22},
      {"start_page": 150, "end_page": 151}
    ],
    "review_status": "pending"
  },
  "problem_solution_structure": {
    "schema_version": "library_instance_problem_solution_structure_v1",
    "structure_mode": "hybrid",
    "solution_status": "identified",
    "exercise_set_id": "practice_04"
  }
}
```

## Response

Returns both normalized selections, derived ranges, structure fields, `changed`, instance metadata and policy.
