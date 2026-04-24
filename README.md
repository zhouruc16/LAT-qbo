# tiktok_qbo

Local pipeline for transforming TikTok LAT settlement .xlsx into QBO-ready JSON.

## Install
    pip install -e ".[dev]"

## Run (stages 1-3; QBO posting not in this plan)
    tiktok_qbo ingest    inputs/lat-PLELNU-2024Q2.xlsx
    tiktok_qbo plan      --hash <H>
    tiktok_qbo reconcile --hash <H>

Outputs land in `state/`.
