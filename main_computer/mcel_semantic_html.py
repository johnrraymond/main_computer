"""Strict, non-executable HTML hierarchy input for the paired MCEL V1 authoring path.

This is a compiler-time semantic projection, NOT a browser renderer or a second
source of operation/authority facts. The HTML must carry no visual layout code.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
from collections.abc import Mapping

PURPOSES = frozenset({
    'workspace', 'navigation', 'inspection', 'commands', 'status', 'results',
    'input', 'mode-control', 'query-controls', 'assistance',
})
TASKS = frozenset({
    'browse', 'locate', 'inspect', 'compare', 'monitor', 'edit', 'create',
    'compute', 'review', 'manage', 'configure', 'diagnose',
})
CONTENT = frozenset({
    'collection', 'record', 'hierarchy', 'document', 'scalar', 'expression',
    'series', 'log', 'diagnostic', 'diff', 'media', 'structured-data',
})
PRIORITIES = frozenset({'primary', 'secondary', 'supporting'})
SIDES = frozenset({'start', 'end', 'top', 'bottom'})
REPRESENTATIONS = frozenset({'automatic', 'tabular', 'graphical', 'textual'})
DENSITIES = frozenset({'automatic', 'compact', 'comfortable'})
RELATIONS = frozenset({
    'selects', 'contextual-to', 'supports', 'reports-for', 'switches-mode-of',
    'acts-on', 'informs', 'accompanies',
})
TAGS = frozenset({
    'main', 'nav', 'section', 'aside', 'header', 'footer', 'article', 'form',
    'output', 'div', 'search', 'details', 'dialog',
})
WRAPPERS = frozenset({'html', 'head', 'body'})
TEXT_TAGS = frozenset({'h1','h2','h3','h4','h5','h6','p','span','strong','em','small','label'})
ALL_TAGS = TAGS | WRAPPERS | TEXT_TAGS
ATTRS = frozenset({
    'data-mcel-id', 'data-mcel-purpose', 'data-mcel-tasks',
    'data-mcel-content-kind', 'data-mcel-content-ref', 'data-mcel-priority',
    'data-mcel-prefer-side', 'data-mcel-prefer-density',
    'data-mcel-prefer-representation',
    'data-mcel-when-state', 'data-mcel-when-value',
})
NATIVE_PURPOSE = {'main':'workspace', 'nav':'navigation', 'aside':'inspection',
                  'output':'results', 'search':'query-controls'}
ID_RE = re.compile(r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')
REF_RE = re.compile(r'^(model|state):[A-Za-z0-9][A-Za-z0-9._:@/-]*$')


class SemanticHtmlError(ValueError):
    def __init__(self, code: str, detail: str, line: int, column: int):
        super().__init__(detail)
        self.code, self.line, self.column = code, line, column


class _SemanticParser(HTMLParser):
    def __init__(self, app_id: str, source: str):
        super().__init__(convert_charrefs=True)
        self.app_id, self.source = app_id, source
        self.stack: list[tuple[str, str | None]] = []
        self.regions: list[dict] = []
        self.by_name: set[str] = set()
        self.child_counts: dict[str | None, int] = {}
        self.regions_by_name: dict[str,dict] = {}
        self.static_stack: list[dict] = []

    def fail(self, code: str, detail: str):
        line, col = self.getpos()
        raise SemanticHtmlError(code, detail, line, col + 1)

    def handle_decl(self, decl):
        if decl.strip().lower() != 'doctype html':
            self.fail('MCEL_HTML_DECLARATION_UNSUPPORTED', 'Only <!doctype html> is permitted.')

    def handle_startendtag(self, tag, attrs):
        self.fail('MCEL_HTML_VOID_MARKUP_UNSUPPORTED', f'Self-closing tag <{tag}/> is not a semantic region.')

    def handle_starttag(self, tag, attrs):
        if tag not in ALL_TAGS:
            self.fail('MCEL_HTML_TAG_UNSUPPORTED', f'Unsupported semantic HTML tag <{tag}>.')
        keys = [key for key, _ in attrs]
        if len(keys) != len(set(keys)):
            self.fail('MCEL_HTML_ATTRIBUTE_DUPLICATE', f'Duplicate attributes on <{tag}>.')
        raw = dict(attrs)
        unknown = sorted(set(raw) - ATTRS)
        if unknown:
            self.fail('MCEL_HTML_ATTRIBUTE_UNSUPPORTED', f'Unsupported HTML attribute(s): {unknown}. Layout and executable markup are forbidden.')
        if tag in WRAPPERS and any(k.startswith('data-mcel-') for k in raw):
            self.fail('MCEL_HTML_WRAPPER_SEMANTICS', 'Document wrappers cannot declare an MCEL semantic region.')
        name = raw.get('data-mcel-id')
        if name is not None and (not ID_RE.fullmatch(name) or name in self.by_name):
            self.fail('MCEL_HTML_REGION_ID_INVALID', f'Invalid or duplicate region ID: {name!r}.')
        if tag in TAGS and not name:
            self.fail('MCEL_HTML_REGION_ID_REQUIRED', f'<{tag}> requires data-mcel-id.')
        if tag in TEXT_TAGS and any(k.startswith('data-mcel-') for k in raw):
            self.fail('MCEL_HTML_TEXT_REGION_UNSUPPORTED', 'Text/heading markup cannot declare a semantic region in this contract.')
        if tag in WRAPPERS and self.stack and self.stack[-1][0] not in ({'html'} if tag in {'head','body'} else set()):
            self.fail('MCEL_HTML_WRAPPER_NESTING', 'Invalid document wrapper nesting.')
        if name:
            if any(ancestor == 'head' for ancestor,_ in self.stack):
                self.fail('MCEL_HTML_HEAD_REGION', 'Semantic regions cannot be placed in <head>.')
            purpose = raw.get('data-mcel-purpose') or NATIVE_PURPOSE.get(tag)
            if purpose is not None and purpose not in PURPOSES:
                self.fail('MCEL_HTML_PURPOSE_INVALID', f'Unsupported purpose: {purpose!r}.')
            # Native landmark meaning is authoritative; conflicting MCEL annotations fail.
            if tag in NATIVE_PURPOSE and raw.get('data-mcel-purpose') not in (None, NATIVE_PURPOSE[tag]):
                self.fail('MCEL_HTML_NATIVE_PURPOSE_CONFLICT', f'<{tag}> conflicts with declared purpose.')
            task_str = raw.get('data-mcel-tasks') or ''
            tasks = task_str.split()
            if len(tasks) != len(set(tasks)) or any(task not in TASKS for task in tasks):
                self.fail('MCEL_HTML_TASK_INVALID', 'Duplicate or unknown task token.')
            kind = raw.get('data-mcel-content-kind')
            ref = raw.get('data-mcel-content-ref')
            if (kind is None) != (ref is None) or (kind is not None and (kind not in CONTENT or not REF_RE.fullmatch(ref))):
                self.fail('MCEL_HTML_CONTENT_INVALID', 'Content kind and typed model/state reference must be declared together.')
            when_state = raw.get('data-mcel-when-state')
            when_value = raw.get('data-mcel-when-value')
            if (when_state is None) != (when_value is None) or (when_state is not None and
                (not re.fullmatch(r'state:[a-z][a-z0-9-]*', when_state) or not when_value or len(when_value) > 80)):
                self.fail('MCEL_HTML_VISIBILITY_INVALID', 'Mode visibility requires a declared state ref and bounded value.')
            priority = raw.get('data-mcel-priority')
            if priority is not None and priority not in PRIORITIES:
                self.fail('MCEL_HTML_PRIORITY_INVALID', 'Invalid semantic priority.')
            prefs = {}
            for attr,key,allowed in (
                ('data-mcel-prefer-side','side',SIDES),
                ('data-mcel-prefer-density','density',DENSITIES),
                ('data-mcel-prefer-representation','representation',REPRESENTATIONS),
            ):
                if attr in raw:
                    if raw[attr] not in allowed:
                        self.fail('MCEL_HTML_PREFERENCE_INVALID', f'Invalid preference: {attr}.')
                    prefs[key] = raw[attr]
            parent = next((n for _,n in reversed(self.stack) if n), None)
            ordinal = self.child_counts.get(parent, 0)
            self.child_counts[parent] = ordinal + 1
            line,col = self.getpos()
            region = {
                'id': f'interface-region:{self.app_id}.{name}',
                'kind': 'interface-region', 'nativeTag':tag,
                'parentId': f'interface-region:{self.app_id}.{parent}' if parent else None,
                'ordinal': ordinal,
                'members': [],
                'htmlSource': {'file':self.source, 'line':line, 'column':col+1},
            }
            if purpose: region['purpose'] = purpose
            if tasks: region['tasks'] = tasks
            if kind: region['content'] = {'kind':kind, 'ref':ref}
            if priority: region['priority'] = priority
            if when_state: region['visibleWhen'] = {'state': {'ref': when_state}, 'equals': when_value}
            if prefs: region['preferences'] = prefs
            self.regions.append(region)
            self.by_name.add(name)
        if tag in TEXT_TAGS:
            parent = next((n for _, n in reversed(self.stack) if n), None)
            if parent is None:
                self.fail('MCEL_HTML_ORPHAN_TEXT', 'Static text must be inside a semantic region.')
            # Each static element preserves its position in the semantic reading
            # order. Text is compiler data, not executable DOM.
            ordinal = self.child_counts.get(parent, 0)
            self.child_counts[parent] = ordinal + 1
            line,col = self.getpos()
            annotation = {'tag':tag,'ordinal':ordinal,'text':'',
                          'source': {'file':self.source,'line':line,'column':col+1}}
            self.regions_by_name[parent].setdefault('staticContent',[]).append(annotation)
            self.static_stack.append(annotation)
        if name:
            self.regions_by_name[name] = region
        self.stack.append((tag,name))

    def handle_data(self, data):
        if not data.strip():
            return
        if self.static_stack:
            self.static_stack[0]['text'] += data
        else:
            self.fail('MCEL_HTML_UNBOUND_TEXT', 'Non-whitespace text must be inside a supported static text element.')

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            self.fail('MCEL_HTML_NESTING_INVALID', f'Unexpected closing </{tag}>.')
        self.stack.pop()
        if tag in TEXT_TAGS and self.static_stack:
            # Nested strong/em/etc contributes to the parent text as well.
            self.static_stack.pop()

    def close_tree(self):
        if self.stack:
            self.fail('MCEL_HTML_NESTING_INVALID', f'Unclosed <{self.stack[-1][0]}>.')
        roots = [r for r in self.regions if r['parentId'] is None]
        if len(roots) != 1:
            self.fail('MCEL_HTML_ROOT_INVALID', 'Exactly one semantic root region is required.')
        main_tags = [r for r in self.regions if r['nativeTag'] == 'main']
        if len(main_tags) > 1:
            self.fail('MCEL_HTML_MAIN_DUPLICATE', 'At most one <main> landmark is permitted.')
        return self.regions


def parse_semantic_html(text: str, *, app_id: str, source: str) -> list[dict]:
    """Return ordered semantic region records; fail closed on unknown markup."""
    parser = _SemanticParser(app_id, source)
    try:
        parser.feed(text)
        parser.close()
        return parser.close_tree()
    except SemanticHtmlError:
        raise
    except Exception as exc:
        raise SemanticHtmlError('MCEL_HTML_PARSE_INVALID', str(exc), *parser.getpos()) from exc
