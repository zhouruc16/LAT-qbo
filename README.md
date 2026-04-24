# tiktok_qbo

Local pipeline for transforming TikTok LAT settlement .xlsx into QBO-ready JSON.

## Install
    pip install -e ".[dev]"

## Run (stages 1-3; QBO posting not in this plan)
    tiktok_qbo ingest    inputs/lat-PLELNU-2024Q2.xlsx
    tiktok_qbo plan      --hash <H>
    tiktok_qbo reconcile --hash <H>

Outputs land in `state/`.

## Smoke test on real data

    python scripts/smoke_q2_2024.py

Reads `~/Downloads/4-6-2024.xlsx`, runs ingest → plan → reconcile, and
prints per-stage counts plus any reconciliation mismatches.
