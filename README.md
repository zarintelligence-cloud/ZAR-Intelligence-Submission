# ZAR Intelligence — Google Cloud prototype

Prototype: https://zar-intelligence-hvrm7qoxgq-el.a.run.app/

This is a reviewed, source-only submission snapshot derived from the working V12 application. It has a new Git history and excludes database exports, credentials, private deployment configuration, customer uploads, and historical database fixtures.

## Architecture

- Python/FastAPI application on Google Cloud Run in Mumbai.
- Firestore stores workflow documents, orders, audit records, and workspace sessions.
- Gemini is called server-side for language reasoning. Pricing, authorization, state transitions, artwork composition, and validation remain deterministic code.
- Existing Supabase Authentication and private file storage are retained during this migration. This release does not claim that authentication or uploaded assets have moved to Firebase.
- Secrets are provided through Google Secret Manager; no secret belongs in frontend code.

Polaris coordinates the retail workflow. Rosetta extracts the brief; Spectra examines artwork; Hubble composes proofs; Pleiades handles the roster; Kepler calculates the quote using policy tables and uses language assistance for explanation. Some domain packs and agents shown as planned are not implemented products.

## Run locally

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ZAR_LLM_MODE=mock ZAR_STORE_MODE=memory ZAR_ASSET_MODE=memory ZAR_DEMO_SEED=0 uvicorn services.polaris.main:app --host 127.0.0.1 --port 8080
```

Local mock mode is for demonstration and tests. Cloud deployment uses `Dockerfile.cloudrun` and `app:app`, which disables development admin access and rejects incomplete runtime configuration.

## Cloud configuration

Set `GOOGLE_CLOUD_PROJECT`, `ZAR_STORE_MODE=firestore`, `ZAR_FIRESTORE_DATABASE`, `ZAR_ASSET_MODE=supabase`, `ZAR_ASSET_BUCKET`, `ZAR_SUPABASE_URL`, `ZAR_SUPABASE_PUBLISHABLE_KEY`, `ZAR_LLM_MODE=gemini`, and supported `ZAR_MODEL_FAST` / `ZAR_MODEL_REASONING` model IDs. Bind `GEMINI_API_KEY` and `ZAR_SUPABASE_SECRET_KEY` from Secret Manager. Use a dedicated runtime service account with database-scoped Firestore access and access only to the required secrets. Deploy the composite indexes in `deploy/firestore.indexes.json`.

## Verification

The migration importer validates row counts, identities, sizes, and existing target conflicts. It defaults to a dry run; `--apply` performs create-only import and verifies the resulting records. Never commit an export. A live website response does not by itself prove that model credentials, merchant sign-in, or production orders work; validate those separately before a domain cutover.

```sh
python -m pytest tests/test_firestore_migration.py tests/test_supabase_store.py tests/test_merchant_console.py -q
```

Production data and passwords are not part of the public demonstration. Sign-in is required for saved orders. The previous production domain remains separate until the final migration cutover and data reconciliation are verified.
