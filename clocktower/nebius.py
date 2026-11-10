"""Token Factory Chat Completions. Capabilities and prices are explicit experiment inputs."""
from __future__ import annotations
import asyncio
import copy
import http.client
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .llm import _Base, HTTP_POOL, TLS, NO_LIMIT, CREDIT_WAIT, Reply, SeatFailed

BASE_URL = 'https://api.tokenfactory.nebius.com/v1'
MODELS = {}


def base_url():
    url = os.environ.get('TOKENFACTORY_BASE_URL', BASE_URL).rstrip('/')
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('TOKENFACTORY_BASE_URL must be an HTTPS URL without credentials, query or fragment')
    return url


def configure(path):
    """Load public model settings; never credentials. No guessed prices or provider defaults."""
    entries = json.loads(Path(path).read_text())
    for model, s in entries.items():
        if not model.startswith('nebius:') or not model.split(':', 1)[1]:
            raise ValueError('Token Factory model ids must start with nebius:')
        allowed = {'window', 'max_output', 'prices', 'memory', 'reasoning_field', 'reasoning_effort'}
        if set(s) - allowed:
            raise ValueError(f'{model}: unknown configuration fields')
        for k in ('window', 'max_output'):
            if type(s.get(k)) is not int or s[k] <= 0:
                raise ValueError(f'{model}: {k} must be a positive integer')
        prices = s.get('prices', {})
        if set(prices) != {'input', 'output', 'read'} or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in prices.values()):
            raise ValueError(f'{model}: supply finite nonnegative input/output/read prices in USD per million tokens')
        if s.get('memory', 'replies') not in ('replies', 'reasoning'):
            raise ValueError(f'{model}: memory must be replies or reasoning')
        if s.get('reasoning_field') not in (None, 'reasoning_content', 'reasoning'):
            raise ValueError(f'{model}: unsupported reasoning field')
        if s.get('memory') == 'reasoning' and not s.get('reasoning_field'):
            raise ValueError(f'{model}: reasoning memory needs a verified reasoning_field')
        if type(s.get('reasoning_effort', False)) is not bool:
            raise ValueError(f'{model}: reasoning_effort must be boolean')
    MODELS.clear()
    MODELS.update(entries)


def model_spec(model):
    if model not in MODELS:
        raise ValueError(f'{model}: supply --nebius-config with verified capabilities and prices')
    return {'memory': 'replies', 'effort': False, 'context': False, 'explicit_cache': False,
            'reasoning_effort': False, **copy.deepcopy(MODELS[model])}


def snapshot():
    return {'base_url': base_url(), 'models': copy.deepcopy(MODELS)} if MODELS else None


def matches(value, schema):
    """Validate the schema subset used by player_schema and memory-check, including nested values."""
    if 'anyOf' in schema:
        return any(matches(value, s) for s in schema['anyOf'])
    types = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}
    if type(value) is not types[schema['type']]:
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if isinstance(value, dict):
        props = schema.get('properties', {})
        return (set(schema.get('required', [])) <= value.keys()
                and (schema.get('additionalProperties', True) or value.keys() <= props.keys())
                and all(matches(v, props[k]) for k, v in value.items() if k in props))
    if isinstance(value, list):
        return all(matches(v, schema['items']) for v in value)
    return True


class NebiusConversation(_Base):
    def __init__(self, system, schema, **kw):
        super().__init__(system, schema, **kw)
        self.settings = model_spec(self.model)
        self.key = os.environ.get('TOKENFACTORY_API_KEY')
        if not self.key:
            raise RuntimeError('TOKENFACTORY_API_KEY is not set')
        self.url = base_url() + '/chat/completions'
        self.slug = self.model.split(':', 1)[1]
        self.messages = [{'role': 'system', 'content': system}]

    def turn_effort(self, low=False):
        return super().turn_effort(low) if self.settings['reasoning_effort'] else 'provider-default'

    def _post(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'})
        # Do not forward a credential to a redirected endpoint.
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=TLS), _NoRedirect())
        with opener.open(req, timeout=900) as response:
            return json.load(response)

    async def _request(self, body):
        waited = attempt = 0
        while True:
            self.ledger.check()
            try:
                return await asyncio.get_running_loop().run_in_executor(HTTP_POOL, self._post, body)
            except urllib.error.HTTPError as e:
                status = e.code
                e.close()
                if status == 402 and waited < CREDIT_WAIT:
                    await asyncio.sleep(30)
                    waited += 30
                    continue
                if (status not in (408, 409, 429) and status < 500) or attempt == 9:
                    raise SeatFailed(f'Nebius HTTP {status}; response body omitted') from None
            except json.JSONDecodeError:
                raise SeatFailed('Nebius returned invalid JSON; billing for the response is unknown') from None
            except (TimeoutError, urllib.error.URLError, ConnectionError, http.client.HTTPException):
                if attempt == 9:
                    raise SeatFailed('Nebius transport failed; billing for lost responses is unknown') from None
            await asyncio.sleep(min(2 ** attempt, 60) + random.random())
            attempt += 1

    def _usage(self, raw):
        u = raw.get('usage') or {}
        read = (u.get('prompt_tokens_details') or {}).get('cached_tokens') or 0
        thinking = (u.get('completion_tokens_details') or {}).get('reasoning_tokens') or 0
        inp, out = u.get('prompt_tokens'), u.get('completion_tokens')
        if (any(type(v) is not int or v < 0 for v in (inp, out, read, thinking))
                or read > inp or thinking > out):
            raise SeatFailed('Nebius usage missing or invalid; reconcile provider billing before resuming')
        usage = {'input_tokens': inp - read, 'cache_read_input_tokens': read, 'output_tokens': out,
                 'output_tokens_details': {'thinking_tokens': thinking}}
        p = self.settings['prices']
        return usage, (p['input'] * (inp - read) + p['read'] * read + p['output'] * out) / 1e6

    async def send(self, text, meta=None, commit=True):
        # A probe owns its trimming and telemetry too; concurrent probes cannot alter game memory.
        if not commit:
            fork = copy.copy(self)
            fork.messages = copy.deepcopy(self.messages)
            fork.semaphore = NO_LIMIT
            return await fork.send(text, meta, commit=True)
        self.fit(text)
        messages = copy.deepcopy(self.messages) + [{'role': 'user', 'content': text}]
        truncations = refusals = 0
        low = False
        for attempt in range(4):
            self.ledger.check()
            body = {'model': self.slug, 'messages': messages, 'max_tokens': self.max_tokens,
                    'response_format': {'type': 'json_schema', 'json_schema': {
                        'name': 'game_action', 'strict': True, 'schema': self.schema}}}
            if self.settings['reasoning_effort']:
                body['reasoning_effort'] = self.turn_effort(low)
            start = time.monotonic()
            async with self.semaphore:
                raw = await self._request(body)
            usage, spent = self._usage(raw)
            self.ledger.add(spent, label=self.label, model=self.model)
            choice = (raw.get('choices') or [{}])[0]
            msg = choice.get('message') or {}
            thinking = msg.get('reasoning_content') or msg.get('reasoning') or ''
            out = msg.get('content') or ''
            stop = {'stop': 'end_turn', 'length': 'max_tokens', 'content_filter': 'refusal'}.get(
                choice.get('finish_reason'), choice.get('finish_reason'))
            if msg.get('refusal'):
                stop = 'refusal'
            self.record(meta, attempt, stop, start, spent, usage, thinking, out,
                        {'provider': 'nebius', 'cost_source': 'configured_rates', 'raw_usage': raw['usage']}, low)
            if stop == 'max_tokens':
                truncations += 1
                low = True
                if truncations < 2:
                    continue
            elif stop == 'refusal':
                refusals += 1
                if refusals < 2:
                    continue
            elif stop == 'end_turn':
                try:
                    action = json.loads(out)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not matches(action, self.schema):
                    continue
                assistant = {'role': 'assistant', 'content': out}
                field = self.settings.get('reasoning_field')
                if self.settings['memory'] == 'reasoning' and field and msg.get(field):
                    assistant[field] = msg[field]
                self.messages = messages + [assistant]
                return Reply(action, thinking)
            else:
                continue
            # Retain the observation when a capped/refused turn passes, without feeding partial output back.
            self.messages = messages
            return Reply({}, thinking)
        raise SeatFailed(f'{self.label} ({self.model}): no usable reply in 4 attempts')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
