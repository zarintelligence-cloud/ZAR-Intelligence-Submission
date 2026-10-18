"""Save onboarding requests without changing tenant routing or fetching user URLs."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
import ipaddress
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from .identity import backend, viewer, writes, scoped, require_role

router = APIRouter(prefix='/api/console/storefront-connections', dependencies=[Depends(writes)])
COLLECTION = 'storefront_connections'

class Connection(BaseModel):
    tenant_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    website: str = Field(min_length=4, max_length=500)
    platform: str = Field(default='custom', pattern=r'^(custom|shopify|wordpress|other)$')

def website_origin(value):
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError()
        if not host or '.' not in host or host.endswith(('.localhost','.local','.internal')):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        if any(not label or len(label)>63 or label.startswith('-') or label.endswith('-') or not all(c.isalnum() or c=='-' for c in label) for label in host.split('.')):
            raise ValueError()
        return 'https://' + host
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Enter a public HTTPS website address, for example https://yourstore.com')

def tenant(user, tid):
    require_role(user, 'administrator')
    scoped(user, tid)
    return backend().tenant_profile(tid)

@router.get('')
def get_connection(tenant_id: str, user=Depends(viewer)):
    profile = tenant(user, tenant_id)
    record = backend().APP.runtime.store.get(COLLECTION, tenant_id)
    return {'connection': record, 'configured_domain': profile.get('domain','')}

@router.post('')
def save_connection(body: Connection, user=Depends(viewer)):
    profile = tenant(user, body.tenant_id)
    origin = website_origin(body.website)
    domain = str(profile.get('domain','')).lower().strip().removeprefix('https://').removeprefix('http://').rstrip('/')
    matching = origin[8:].removeprefix('www.') == domain.removeprefix('www.')
    record = {'id': body.tenant_id, 'tenant_id': body.tenant_id, 'website': origin,
              'platform': body.platform, 'status': 'ready_to_install' if matching else 'onboarding_required',
              'verified': False, 'updated_at': datetime.now(timezone.utc).isoformat(), 'updated_by': user['id']}
    backend().APP.runtime.store.set(COLLECTION, body.tenant_id, record)
    return {'connection': record, 'configured_domain': profile.get('domain','')}
