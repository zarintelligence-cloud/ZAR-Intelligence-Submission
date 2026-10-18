"""Vercel entry point. Keep unconfigured cloud instances read-only."""
import os
import secrets
import hashlib
import hmac
from contextlib import asynccontextmanager

# Never expose development admin bypasses on a public function.
os.environ['ZAR_DEMO_MODE'] = '0'
_server_secret = os.getenv('ZAR_SUPABASE_SECRET_KEY')
os.environ.setdefault('ZAR_ADMIN_TOKEN',
    hmac.new(_server_secret.encode(), b'zar-vercel-internal-signing-v1', hashlib.sha256).hexdigest()
    if _server_secret else secrets.token_urlsafe(48))
del _server_secret
os.environ.setdefault('ZAR_DATA_DIR', '/tmp/zar')

from fastapi.responses import HTMLResponse, JSONResponse
from services.polaris.main import app, STATIC
from zar_core import config

# Cloud functions do not guarantee execution after a response. Use an external
# scheduler for retries when a fully configured production backend is attached.
@asynccontextmanager
async def function_lifespan(application):
    yield

app.router.lifespan_context = function_lifespan

def configured():
    return bool(
        config.STORE_MODE in {'supabase', 'firestore'}
        and (config.STORE_MODE != 'firestore' or os.getenv('GOOGLE_CLOUD_PROJECT'))
        and config.SUPABASE_URL and config.SUPABASE_SECRET_KEY
        and os.getenv('ZAR_SUPABASE_PUBLISHABLE_KEY')
        and config.ASSET_MODE in {'gcs', 'supabase'} and config.ASSET_BUCKET
    )

@app.middleware('http')
async def require_cloud_configuration(request, call_next):
    if configured() and config.LLM_MODE != 'gemini' and request.url.path == '/' and request.method in {'GET', 'HEAD'}:
        page = (STATIC / 'website-v12.html').read_text(encoding='utf-8')
        notice = '<div role="status" style="background:#503d13;color:#fff;padding:12px;text-align:center;font:14px system-ui">Setup mode — database connected; Gemini is not configured. Use test orders only.</div>'
        return HTMLResponse(page.replace('<body>', '<body>'+notice, 1), headers={'Cache-Control':'no-store'})
    if not configured():
        path = request.url.path
        if request.method in {'GET', 'HEAD'} and path == '/':
            page = (STATIC / 'website-v12.html').read_text(encoding='utf-8')
            notice = '<div role="status" style="background:#503d13;color:#fff;padding:12px;text-align:center;font:14px system-ui">Website preview — workspace sign-in and order submission are not connected yet.</div>'
            page = page.replace('<body>', '<body>'+notice, 1)
            return HTMLResponse(page, headers={'Cache-Control':'no-store'})
        if path == '/api/console/config':
            return JSONResponse({'auth':False,'local':False,'store':'not connected',
                'llm':'mock','models':{},'release':config.RELEASE_ID})
        if path == '/api/console/me':
            return JSONResponse({'detail':'Please sign in'},status_code=401)
        if request.method not in {'GET','HEAD'} or path.startswith(('/api/','/v1/')):
            if path != '/api/console/rates':
                return JSONResponse({'detail':'Workspace setup is not complete. Orders and enquiries are not being accepted yet.'},status_code=503)
    return await call_next(request)
