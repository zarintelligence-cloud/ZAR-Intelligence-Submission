"""How the runtime reaches capabilities: in-process (monolith/dev) or private HTTP service."""
from __future__ import annotations
from .contracts import CapabilityRequest, CapabilityResult
from .registry import CapabilityRegistry


class LocalInvoker:
    remote = False

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def invoke(self, req: CapabilityRequest) -> CapabilityResult:
        return self.registry.invoke(req)


class RemoteInvoker:
    """Calls the private workshop with a Google-signed OIDC ID token (Cloud Run IAM)."""
    remote = True

    def __init__(self, base_url: str, timeout: int = 120) -> None:
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def _headers(self) -> dict[str, str]:
        from urllib.parse import urlsplit
        url = urlsplit(self.base_url)
        if url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}:
            return {}
        import google.auth.transport.requests
        import google.oauth2.id_token
        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), self.base_url)
        return {"Authorization": f"Bearer {token}"}

    def invoke(self, req: CapabilityRequest) -> CapabilityResult:
        import requests
        try:
            r = requests.post(f"{self.base_url}/v1/invoke", json=req.model_dump(),
                              headers=self._headers(), timeout=self.timeout)
            r.raise_for_status()
            return CapabilityResult(**r.json())
        except Exception as exc:
            return CapabilityResult(request_id=req.request_id, capability=req.capability,
                                    status="error", error=f"Workshop call failed: {type(exc).__name__}")


class A2AInvoker(RemoteInvoker):
    """Blocking A2A JSON-RPC message/send; no discovery or arbitrary remote URLs."""
    def invoke(self, req):
        import requests
        from .adk_agents import SPECIALISTS
        name = SPECIALISTS.get(req.capability) if req.context.domain == 'retail' else None
        if not name:
            return CapabilityResult(request_id=req.request_id, capability=req.capability,
                                    status='error', error='No A2A agent for capability')
        body = {'jsonrpc': '2.0', 'id': req.request_id, 'method': 'message/send', 'params': {
            'message': {'role': 'user', 'messageId': req.request_id,
                        'parts': [{'kind': 'text', 'text': req.model_dump_json()}]},
            'configuration': {'blocking': True}}}
        try:
            response = requests.post(f'{self.base_url}/a2a/{name}/', json=body,
                                     headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            rpc = response.json()
            if 'error' in rpc:
                raise ValueError('A2A protocol error')
            result = rpc['result']
            parts = result.get('parts', [])
            for artifact in result.get('artifacts', []):
                parts.extend(artifact.get('parts', []))
            # Only a final artifact/message is accepted, never an interim progress message.
            if result.get('status', {}).get('state', 'completed') != 'completed':
                raise ValueError('A2A task did not complete')
            parsed = CapabilityResult.model_validate_json(''.join(p.get('text', '') for p in parts))
            if parsed.request_id != req.request_id or parsed.capability != req.capability:
                raise ValueError('A2A result identity mismatch')
            return parsed
        except Exception as exc:
            return CapabilityResult(request_id=req.request_id, capability=req.capability,
                                    status='error', error=f'A2A call failed: {type(exc).__name__}')
