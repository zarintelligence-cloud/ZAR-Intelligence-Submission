"""Private capability host ('workshop'). Cloud Run: --no-allow-unauthenticated; only Polaris may invoke."""
from fastapi import FastAPI
from zar_core.bootstrap import build
from zar_core.contracts import CapabilityRequest, CapabilityResult
from zar_core import config
from zar_core.adk_agents import mount_a2a

APP = build("workshop")
app = FastAPI(title="ZAR Workshop - capability host", version=APP.releases.items["zar-core"].version)
mount_a2a(app, APP.registry, APP.runtime.store, APP.runtime.assets, config.WORKSHOP_URL)


@app.get("/healthz")
def health():
    return {"ok": True, "release": APP.releases.manifest(), "compatibility": APP.releases.compatibility_check()}


@app.get("/v1/capabilities")
def capabilities():
    return APP.registry.describe()


@app.post("/v1/invoke", response_model=CapabilityResult)
def invoke(req: CapabilityRequest):
    return APP.registry.invoke(req)
