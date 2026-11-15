# Engine continuation fixtures

This directory contains the 45 immutable files still read by delivery verification or review tests after the historical `outputs/engine-continuation-20261002` workspace was archived. The files preserve their old relative layout so a verifier can use one fixture root. Source and destination SHA-256 values are recorded in `benchmarks/archive-manifests/2026-10-21-engine-continuation-retained-set.json`.

Verification scripts may continue writing fresh evidence below `outputs/`; they must read frozen historical inputs from this fixture directory.

