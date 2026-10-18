# ZAR Intelligence — Google Cloud prototype

Prototype: https://zar-intelligence-hvrm7qoxgq-el.a.run.app/


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
.
