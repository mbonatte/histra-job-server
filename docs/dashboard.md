# Dashboard data contract

The dashboard reads the accepted records stored in PostgreSQL:

- `jobs.job_definition`
- `job_attempts.results_json`
- `job_attempts.run_json`
- `job_attempts.validation_json`
- job, attempt, worker and artifact metadata

It does not read the local HiStrA `.Results` database or parse artifact files in the browser.

## Selected attempt

For a job, the dashboard uses `current_attempt_id` when available. Otherwise, it uses the most recently created attempt. Statistical calculations include completed attempts only.

## Histories

Reaction histories are read from:

```text
results_json.analyses.<analysis>.outputs.reactions
```

Supported columns are discovered from the rows. The current runner normally emits `Step`, `R1`, `R2` and `R3`.

Displacement histories are read from:

```text
results_json.analyses.<analysis>.outputs.displacements
```

Rows are grouped by `IdElement`. The current runner normally emits `Step`, `IdElement`, `ParentKey`, `Ux`, `Uy` and `Uz`.

Modal contributions and scour/interface mutation evidence are displayed when present.

## Statistical metric definitions

- `duration_seconds`: `run_json.duration_seconds`; if absent, attempt finish time minus start time.
- `reaction_final`: selected component at the greatest `Step`.
- `reaction_peak_abs`: largest absolute value of the selected component.
- `reaction_minimum`: smallest signed value of the selected component.
- `reaction_maximum`: largest signed value of the selected component.
- `displacement_final`: selected component at the greatest `Step`, grouped by model point.
- `displacement_peak_abs`: largest absolute value, grouped by model point.
- `displacement_minimum`: smallest signed value, grouped by model point.
- `displacement_maximum`: largest signed value, grouped by model point.

The server returns the step at which each observation was obtained.

## Descriptive statistics

The API returns:

- count;
- arithmetic mean;
- median;
- sample standard deviation;
- coefficient of variation;
- minimum and maximum;
- first and third quartiles;
- fifth and ninety-fifth percentiles.

Percentiles use linear interpolation over the sorted observations.

## Numeric scenario metadata

The catalog discovers numeric fields recursively from:

```text
job_definition.metadata
run_json.metadata
```

These fields can be used for grouping or as the X variable of a scatter plot. Store the bridge, material, load and scour variables needed for future comparisons under `metadata` using stable field names.

## Current scaling model

The initial dashboard computes scientific metrics in the FastAPI process from accepted JSON. This is appropriate for the current trusted-colleague workflow and keeps the existing database schema unchanged.

When the campaign grows substantially, the same API can be backed by normalised response tables or materialised views without changing the browser interface.
