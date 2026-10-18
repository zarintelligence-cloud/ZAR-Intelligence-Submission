"""V7 overview preferences and grounded, tenant-scoped knowledge retrieval."""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from zar_core.contracts import utcnow
from .identity import viewer, writes, backend, scoped, require_role

router = APIRouter(prefix='/api/console', dependencies=[Depends(writes)])
ROOT = Path(__file__).resolve().parents[2]

def catalog():
    return json.loads((ROOT/'domains/retail/overview_catalog.json').read_text(encoding='utf-8'))

class Preferences(BaseModel):
    overview_tab: Literal['map', 'dash', 'chart'] = 'map'
    storefront: str = Field('all', max_length=64)

@router.get('/preferences')
def get_preferences(user=Depends(viewer)):
    saved = backend().APP.runtime.store.get('console_preferences', user['id']) or {}
    value = Preferences(**saved)
    if value.storefront != 'all' and value.storefront not in user['stores']:
        value.storefront = 'all'
    return value.model_dump()

@router.post('/preferences')
def save_preferences(body: Preferences, user=Depends(viewer)):
    if body.storefront != 'all': scoped(user, body.storefront)
    backend().APP.runtime.store.set('console_preferences', user['id'], body.model_dump())
    return body.model_dump()

@router.get('/overview-catalog')
def overview_catalog(user=Depends(viewer)):
    return catalog()

def knowledge_sources(user, tenant):
    scoped(user, tenant)
    rt = backend().APP.runtime
    sources = []
    for name in ['pricing', 'catalog', 'print_policy', 'question_policy', 'fulfillment']:
        path = ROOT/'domains/retail/policy'/f'{name}.yaml'
        if path.exists():
            sources.append({'id': name, 'name': name.replace('_', ' ').title(), 'kind':'Versioned policy',
                            'text':path.read_text(encoding='utf-8'), 'reference':f'domains/retail/policy/{name}.yaml'})
    notes = [n for n in rt.store.list('knowledge_notes', 'tenant_id', tenant, limit=500) if not n.get('archived')]
    sources.extend({'id':n['id'], 'name':n['title'], 'kind':'Team reference note', 'text':n['text'],
                    'reference':'note:'+n['id']} for n in notes)
    return sources

@router.get('/knowledge')
def knowledge(tenant: str = 'store6', user=Depends(viewer)):
    return {'sources':knowledge_sources(user, tenant),
            'notice':'Team notes are reference material. They do not override pricing, approval or safety rules.'}

class Note(BaseModel):
    tenant_id: str = Field('store6', max_length=64)
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=4000)

@router.post('/knowledge/notes', status_code=201)
def add_note(body: Note, user=Depends(viewer)):
    require_role(user, 'administrator')
    scoped(user, body.tenant_id)
    if not body.title.strip() or not body.text.strip(): raise HTTPException(422, 'Enter a title and note')
    key = uuid.uuid4().hex
    backend().APP.runtime.store.set('knowledge_notes', key, {'id':key, **body.model_dump(),
        'actor_id':user['id'], 'created_at':utcnow(), 'archived':False})
    return {'id':key}

@router.post('/knowledge/notes/{key}/archive')
def archive_note(key: str, user=Depends(viewer)):
    require_role(user, 'administrator')
    store = backend().APP.runtime.store
    note = store.get('knowledge_notes', key)
    if not note: raise HTTPException(404, 'Note not found')
    scoped(user, note['tenant_id'])
    store.update('knowledge_notes', key, {'archived':True, 'archived_at':utcnow(), 'archived_by':user['id']})
    return {'ok':True}

class Question(BaseModel):
    tenant_id: str = Field('store6', max_length=64)
    question: str = Field(min_length=3, max_length=1000)

@router.post('/knowledge/ask')
def ask(body: Question, user=Depends(viewer)):
    sources = knowledge_sources(user, body.tenant_id)
    q = body.question.lower()
    store = backend().APP.runtime.store
    orders = store.list('sessions','tenant_id',body.tenant_id,limit=1000)
    waiting = {'asking','awaiting_approval','collecting','awaiting_confirmation'}
    if any(w in q for w in ['waiting','pending','awaiting']):
        found = [o for o in orders if o['state'] in waiting and not o.get('manual_hold') and not o.get('retry_step')]
        return {'answer':f'{len(found)} orders are waiting for customer input in the available record window.',
            'sources':[{'label':o.get('title') or o['session_id'], 'text':o['state'],
                        'href':'#/app/retail/orders/'+o['session_id']} for o in found[:20]], 'method':'saved-record retrieval'}
    tokens = set(re.findall(r'[a-z0-9]{3,}',q)) - {'the','and','what','how','our','are','for','from','does','that','this'}
    matches=[]
    for o in orders:
        title=o.get('title') or (o.get('transcript') or [''])[0]
        score=100 if o['session_id'] in q else len(tokens & set(re.findall(r'[a-z0-9]{3,}',title.lower())))
        if score:
            quote=(o.get('quote') or {}).get('quote') or {}
            text=f"State: {o['state']}. Quantity: {(o.get('spec') or {}).get('quantity','not supplied')}."
            if quote: text+=f" Saved quote: INR {quote['total']}."
            matches.append((score,{'label':title or o['session_id'],'text':text,'href':'#/app/retail/orders/'+o['session_id']}))
    for s in sources:
        score=len(tokens & set(re.findall(r'[a-z0-9]{3,}',(s['name']+' '+s['text']).lower())))
        if score: matches.append((score,{'label':s['name'],'text':s['text'],'reference':s['reference']}))
    matches.sort(key=lambda pair:pair[0],reverse=True)
    return {'answer':'Matching saved records and policy sources are shown below.' if matches else 'No matching source found. Try an order ID, policy name or a more specific question.',
            'sources':[m[1] for m in matches[:5]], 'method':'keyword retrieval; source text, not an AI-generated answer'}

class Estimate(BaseModel):
    tenant_id: str = Field('store6', max_length=64)
    garment: str
    method: str
    quantity: int = Field(ge=1, le=10000)
    personalisation: bool = False

@router.post('/knowledge/estimate')
def estimate(body: Estimate, user=Depends(viewer)):
    scoped(user, body.tenant_id)
    from domains.retail.pricing import calculate
    try:
        result=calculate({'quantity':body.quantity,'garment_type':body.garment,
                          'personalisation':['names'] if body.personalisation else []},body.method)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    return {'quote':result,'notice':'Policy estimate only. Final orders require artwork, roster and approval.',
            'sources':['domains/retail/policy/pricing.yaml','domains/retail/policy/catalog.yaml']}
