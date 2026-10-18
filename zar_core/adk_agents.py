"""Google ADK adapters around the versioned ZAR workflow and specialist contracts.

ADK handles execution and A2A interoperability. ZAR retains durable business state,
authorization, policy arithmetic and idempotency; an LLM cannot bypass those gates.
"""
from __future__ import annotations
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, Callable
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr
from .contracts import CapabilityRequest, CapabilityResult
from .services import services

SPECIALISTS = {
    'requirements_analysis': 'rosetta', 'artwork_analysis': 'spectra',
    'proof_generation': 'hubble', 'roster_management': 'pleiades',
    'pricing': 'kepler', 'fulfillment_confidence': 'horizon',
    'compose_reply': 'voice', 'order_handoff': 'handoff',
}


class ContractAgent(BaseAgent):
    _execute: Callable = PrivateAttr()

    def __init__(self, name: str, description: str, execute: Callable):
        super().__init__(name=name, description=description)
        self._execute = execute

    async def _run_async_impl(self, ctx):
        text = ''.join(p.text or '' for p in (ctx.user_content.parts or []))
        payload = json.loads(text)
        # Capabilities use synchronous SDKs. Do not block the A2A event loop.
        result = await asyncio.to_thread(self._execute, payload)
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role='model', parts=[types.Part(text=json.dumps(result))]))


def specialist_agent(capability, registry, store, assets):
    def execute(payload):
        req = CapabilityRequest.model_validate(payload)
        if req.context.domain != 'retail' or req.capability != capability:
            return CapabilityResult(request_id=req.request_id, capability=req.capability,
                                    status='error', error='Capability outside this agent scope').model_dump()
        with services.use(store, assets):
            return registry.invoke(req).model_dump()
    return ContractAgent(SPECIALISTS[capability], f'ZAR retail {capability}; accepts a CapabilityRequest JSON object.', execute)


def run_contract(agent: ContractAgent, payload: dict[str, Any]):
    """One short ADK invocation. Durable sessions remain in the configured ZAR store."""
    async def run():
        sessions = InMemorySessionService()
        runner = Runner(agent=agent, app_name='zar', session_service=sessions)
        session = await sessions.create_session(app_name='zar', user_id='internal')
        try:
            async for event in runner.run_async(user_id='internal', session_id=session.id,
                    new_message=types.Content(role='user', parts=[types.Part(text=json.dumps(payload))])):
                if event.is_final_response() and event.content:
                    return json.loads(''.join(p.text or '' for p in event.content.parts))
            raise RuntimeError('ADK returned no final response')
        finally:
            await runner.close()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run())
    context = copy_context()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(context.run, lambda: asyncio.run(run())).result()


def run_polaris(runtime, session_id, event, data, idempotency_key):
    agent = ContractAgent('polaris', 'ZAR orchestrator: execute the authorized, locked workflow transition.',
        lambda p: runtime._handle_event(p['session_id'], p['event'], p['data'], p['key']))
    return run_contract(agent, {'session_id': session_id, 'event': event, 'data': data, 'key': idempotency_key})


class ADKLocalInvoker:
    remote = False

    def __init__(self, registry):
        self.registry = registry

    def invoke(self, req):
        if req.context.domain != 'retail' or req.capability not in SPECIALISTS:
            return self.registry.invoke(req)
        agent = specialist_agent(req.capability, self.registry, services.store, services.assets)
        return CapabilityResult.model_validate(run_contract(agent, req.model_dump()))


def mount_a2a(app, registry, store, assets, base_url):
    from contextlib import AsyncExitStack, asynccontextmanager
    from urllib.parse import urlsplit
    from google.adk.a2a.utils.agent_to_a2a import to_a2a
    url = urlsplit(base_url)
    children = []
    for capability, name in SPECIALISTS.items():
        agent = specialist_agent(capability, registry, store, assets)
        child = to_a2a(agent, host=url.hostname, port=url.port or (443 if url.scheme == 'https' else 80),
                       protocol=url.scheme, rpc_path=f'a2a/{name}')
        children.append(child)
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(parent):
        added = []
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(previous_lifespan(parent))
            for child in children:
                # ADK creates its routes during lifespan startup, not at construction.
                await stack.enter_async_context(child.router.lifespan_context(child))
                added.extend(child.routes)
            parent.router.routes.extend(added)
            try:
                yield
            finally:
                parent.router.routes[:] = [r for r in parent.router.routes if r not in added]
    app.router.lifespan_context = lifespan
