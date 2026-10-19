"""Patch 18: opt-in, locally authorized external Calculator capabilities.

Only a configured loopback Ollama instance and a separately invoked Mathics3
worker can generate live receipts. No fabricated answer can qualify as proof.
This module does not import or call a Calculator application host/controller.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from main_computer.mcel_portable_workspace import PortableCapabilityRefusal

EXTERNAL_INTENTS = frozenset({
    'askModelForExpression', 'askModelForGraphExpression',
    'askModelForMathicsExpression', 'evaluateMathics', 'askResultQuestion',
})
SAFE_MATHICS_NAMES = frozenset({
    'Expand', 'Factor', 'Simplify', 'FullSimplify', 'D', 'Integrate',
    'Sin', 'Cos', 'Tan', 'Sqrt', 'Log', 'Exp', 'N', 'Solve',
    'x', 'y', 'z', 'Pi', 'E',
})
MATHICS_TOKENS = re.compile(r'\s+|[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?|==|[-+*/^(),\[\]{}]')
GRAPH_CHARS = re.compile(r'^[a-zA-Z0-9\s+*/^().,%\-]+$')
ARITH_CHARS = re.compile(r'^[\d\s+*/^().%\-]+$')

# Conservative, non-evaluating syntax preflight for the pure Calculator graph
# grammar. Chromium still evaluates against calculator-core.js as final authority.
# In particular, Python's syntax parser rejects 2x instead of letting a
# character allowlist issue a receipt for an expression the core cannot parse.
_GRAPH_ONE_ARG = frozenset({
    'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sqrt', 'abs', 'log',
    'ln', 'exp', 'floor', 'ceil', 'round',
})
_GRAPH_VARIABLES = frozenset({'x', 'pi', 'e'})
_GRAPH_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)


def validate_graph_expression(expression: str) -> str:
    """Refuse invalid syntax without executing the model's generated program.

    This preflight deliberately accepts a subset of calculator-core.js grammar.
    All successful live proofs still require the core to parse and sample it.
    """
    if (not isinstance(expression, str) or not expression or len(expression) > 1000
            or not GRAPH_CHARS.fullmatch(expression) or '**' in expression):
        raise PortableCapabilityRefusal('model-graph-expression-invalid-characters')
    try:
        # ^ is exponentiation only in the Calculator graph grammar.
        tree = ast.parse(expression.replace('^', '**'), mode='eval')
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise PortableCapabilityRefusal('model-graph-expression-not-parseable') from exc
    budget = [0]

    def check(node: ast.AST, depth: int = 0) -> None:
        budget[0] += 1
        if budget[0] > 256 or depth > 60:
            raise PortableCapabilityRefusal('model-graph-expression-too-complex')
        if isinstance(node, ast.Expression):
            check(node.body, depth + 1)
        elif isinstance(node, ast.Constant):
            if (type(node.value) not in (int, float)
                    or (type(node.value) is float and not math.isfinite(node.value))
                    or (type(node.value) is int and node.value.bit_length() > 1023)):
                raise PortableCapabilityRefusal('model-graph-expression-invalid-literal')
        elif isinstance(node, ast.Name):
            if node.id not in _GRAPH_VARIABLES:
                raise PortableCapabilityRefusal('model-graph-expression-unsupported-name')
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            check(node.operand, depth + 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _GRAPH_BINARY):
            check(node.left, depth + 1)
            check(node.right, depth + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if (node.keywords or (name in _GRAPH_ONE_ARG and len(node.args) != 1)
                    or (name in ('min', 'max') and not node.args)
                    or (name not in _GRAPH_ONE_ARG and name not in ('min', 'max'))):
                raise PortableCapabilityRefusal('model-graph-expression-invalid-function')
            for arg in node.args:
                check(arg, depth + 1)
        else:
            raise PortableCapabilityRefusal('model-graph-expression-not-parseable')
    check(tree)
    return expression



def _sha(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def validate_mathics_expression(expression: Any) -> str:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 1000:
        raise PortableCapabilityRefusal('mathics-expression-out-of-bounds')
    source = expression.strip()
    tokens = list(MATHICS_TOKENS.finditer(source))
    if not tokens or ''.join(m.group() for m in tokens) != source:
        raise PortableCapabilityRefusal('mathics-expression-unsafe-token')
    if any(t.group().isidentifier() and t.group() not in SAFE_MATHICS_NAMES for t in tokens):
        raise PortableCapabilityRefusal('mathics-expression-unsafe-identifier')
    if source.count('[') != source.count(']') or source.count('(') != source.count(')'):
        raise PortableCapabilityRefusal('mathics-expression-unbalanced')
    return source


def _bounded(p: Mapping[str, Any], name: str, limit: int = 1000) -> str:
    value = p.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise PortableCapabilityRefusal('missing-or-invalid-'+name)
    return value.strip()


def _single_line(text: Any, intent: str) -> str:
    if not isinstance(text, str):
        raise PortableCapabilityRefusal('external-empty-result:'+intent)
    result = text.strip()
    # Accept a code fence only when the entire response is a *single* fenced
    # expression line. Never mine executable-looking substrings from prose.
    fenced = re.fullmatch(r'```(?:math|mathematica|text|plaintext)?[ \t]*\r?\n([^\r\n]+)\r?\n```', result, re.I)
    if fenced:
        result = fenced.group(1).strip()
    else:
        result = result.strip('`').strip()
    if not result or len(result) > 1000 or '\n' in result or '\r' in result:
        raise PortableCapabilityRefusal('external-unbounded-result:'+intent)
    if intent == 'askModelForExpression' and not ARITH_CHARS.fullmatch(result):
        raise PortableCapabilityRefusal('model-arithmetic-expression-not-parseable')
    if intent == 'askModelForGraphExpression':
        validate_graph_expression(result)
    if intent == 'askModelForMathicsExpression':
        validate_mathics_expression(result)
    return result


class PortableCalculatorExternalAuthority:
    """Real backends only. Construction alone does not authorize invocation.

    The operator must explicitly pass its handlers to PortableWorkspaceBroker.
    Receipts are retained independently from browser reports for correlation.
    """

    def __init__(self, *, repo_root: Path, model: str, ollama_url: str = 'http://127.0.0.1:11434',
                 timeout_s: float = 45.0):
        parsed = urlsplit(ollama_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or not parsed.port):
            raise PortableCapabilityRefusal('ollama-loopback-authorization-required')
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', model):
            raise PortableCapabilityRefusal('explicit-model-id-required')
        self.repo_root = Path(repo_root).resolve(strict=True)
        self.model = model
        self.url = ollama_url.rstrip('/')
        self.timeout_s = min(120.0, max(5.0, float(timeout_s)))
        self.records: list[dict[str, Any]] = []
        self.model_attempts: list[dict[str, Any]] = []  # hashed diagnostics, never raw text

    def handlers(self) -> dict[str, Any]:
        return {intent: (lambda p, intent=intent: self.execute(intent, p)) for intent in EXTERNAL_INTENTS}

    def _ollama(self, intent: str, system: str, prompt: str) -> str:
        # An LLM may return prose or a reasoning preamble even when it was
        # instructed to return a one-line expression. Retry formatting up to
        # two times, but never certify guessed, extracted, or unsafe content.
        class DenyRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise PortableCapabilityRefusal('ollama-redirect-not-authorized')
        opener = build_opener(ProxyHandler({}), DenyRedirect())
        for attempt in range(1, 4):
            correction = ('' if attempt == 1 else
                '\nYour previous response did not satisfy the strict one-line output grammar. '
                'Retry the original request, without reasoning, explanation, markdown, '
                'a leading label, or a trailing equals sign. Output exactly ONE expression '
                'or numeric answer. Never include extra lines.'
                + (' For a graph expression, write explicit multiplication: 2*x, NEVER 2x; '
                   'use only functions such as sin(x), cos(x), and operators + - * / % ^.'
                   if intent == 'askModelForGraphExpression' else ''))
            body = {'model': self.model, 'stream': False, 'think': False,
                    'messages': [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': prompt + correction}],
                    'options': {'temperature': 0, 'num_predict': 128}}
            request = Request(self.url+'/api/chat', data=json.dumps(body).encode('utf-8'),
                              headers={'Content-Type':'application/json'}, method='POST')
            try:
                with opener.open(request, timeout=self.timeout_s) as response:
                    raw = response.read(128_001)
                    if len(raw) > 128_000:
                        raise PortableCapabilityRefusal('model-response-too-large')
                    output = json.loads(raw)
            except (OSError, HTTPError, URLError, ValueError) as exc:
                raise PortableCapabilityRefusal('ollama-transport-or-json-error:'+type(exc).__name__) from exc
            if not isinstance(output, dict) or output.get('model') != self.model:
                raise PortableCapabilityRefusal('ollama-model-identity-mismatch')
            message = output.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content'),str):
                raise PortableCapabilityRefusal('ollama-no-content')
            if output.get('done') is not True:
                raise PortableCapabilityRefusal('ollama-generation-incomplete')
            text = message['content']
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
            try:
                result = _single_line(text, intent)
            except PortableCapabilityRefusal as exc:
                self.model_attempts.append({'intent': intent, 'attempt': attempt,
                    'accepted': False, 'rejection': str(exc), 'rawChars': len(text),
                    'rawLines': len(text.splitlines()), 'rawSha256': digest})
                # Unsafe symbolic tokens are a security refusal, not a prompt
                # formatting problem that the model is allowed to negotiate.
                if (attempt == 3 or str(exc).startswith('mathics-expression-')):
                    raise PortableCapabilityRefusal(
                        'model-output-refused-after-'+str(attempt)+'-attempts:'
                        +intent+':'+str(exc)) from exc
                continue
            self.model_attempts.append({'intent': intent, 'attempt': attempt,
                'accepted': True, 'rawChars': len(text),
                'rawLines': len(text.splitlines()), 'rawSha256': digest,
                'normalizedSha256': _sha(result)})
            return result
        raise PortableCapabilityRefusal('model-output-attempts-exhausted:'+intent)

    def _mathics(self, expression: str) -> str:
        expr = validate_mathics_expression(expression)
        try:
            proc = subprocess.run(
                [sys.executable, '-m', 'tools.mcel_portable_mathics_worker'],
                input=json.dumps({'expression': expr}), text=True,
                encoding='utf-8', errors='replace', capture_output=True,
                timeout=self.timeout_s, cwd=self.repo_root, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PortableCapabilityRefusal('mathics-worker-unavailable-or-timed-out') from exc
        if proc.returncode != 0 or len(proc.stdout) > 128_000:
            raise PortableCapabilityRefusal('mathics-worker-refused')
        try:
            output = json.loads(proc.stdout)
        except ValueError as exc:
            raise PortableCapabilityRefusal('mathics-worker-invalid-json') from exc
        if (not isinstance(output,dict) or output.get('ok') is not True
                or output.get('expression') != expr or not isinstance(output.get('result_text'),str)
                or not output['result_text'].strip() or output.get('errors')):
            raise PortableCapabilityRefusal('mathics-evaluation-not-proven')
        return output['result_text'].strip()

    def execute(self, intent: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if intent not in EXTERNAL_INTENTS or not isinstance(payload, Mapping):
            raise PortableCapabilityRefusal('external-intent-not-authorized')
        p = dict(payload)
        context = p.get('context')
        if not isinstance(context, Mapping):
            raise PortableCapabilityRefusal('missing-calculator-context')
        if intent == 'evaluateMathics':
            expression = validate_mathics_expression(_bounded(p,'expression'))
            text = self._mathics(expression)
            backend = 'mathics3-local-worker'
            input_value = expression
        else:
            key = 'question' if intent == 'askResultQuestion' else 'prompt'
            input_value = _bounded(p,key)
            if intent == 'askModelForExpression':
                system = ('Translate the request into a single calculator arithmetic expression. '
                          'Output only digits, parentheses, decimal points, +, -, *, / or %. No equals sign or prose.')
            elif intent == 'askModelForGraphExpression':
                system = ('Translate into one graphing expression using x. Return only the expression, '
                          'with no f(x)= prefix, explanation, or markdown. '
                          'Use the exact executable Calculator graph syntax: explicit * for multiplication '
                          '(2*x, never 2x); supported variable x and functions sin(x), cos(x), etc.')
            elif intent == 'askModelForMathicsExpression':
                system = ('Return one Mathics3 expression using safe symbolic functions (e.g. Expand). '
                          'Return only the expression; no markdown or prose.')
            else:
                system = ('Answer the user question using the supplied calculator context. '
                          'For numeric questions reply with just the number.')
            context_summary = json.dumps({k:context.get(k) for k in
                ('expression','result','graphExpression','mathicsExpression')},
                ensure_ascii=False, default=str)[:3000]
            text = self._ollama(intent,system,input_value+'\nCalculator context: '+context_summary)
            backend = 'ollama-loopback:'+self.model
        evidence = {'schema':'mcel.external-provider-receipt.v1', 'intent':intent,
                    'backend':backend, 'inputSha256':_sha(input_value),
                    'outputSha256':_sha(text), 'verifiedExecution':True}
        record = {'intent':intent,'backend':backend,'inputSha256':evidence['inputSha256'],
                  'outputSha256':evidence['outputSha256'], 'text':text}
        self.records.append(record)
        return {'text':text,'provider':backend,'evidence':evidence}
