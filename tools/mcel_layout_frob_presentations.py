"""Generic fixture-only semantic row presentation adapter for MCEL layout experiments.

Reads row contracts; emits inert DOM structural specimens, not production bindings.
"""
from __future__ import annotations
import html


def catalog(design, width):
    entities=design['semantics']['entities']
    rows=design['rows']['types']
    result=[]
    presentation='narrow_card' if width<=600 else 'table_row'
    for key,spec in rows.items():
        entity=spec.get('entity')
        if entity not in entities:
            raise ValueError(f'row {key}: unknown semantic entity {entity!r}')
        primary=spec.get('primary', [])
        secondary=spec.get('summary', [])
        if not isinstance(primary,list) or not primary:
            raise ValueError(f'row {key}: no primary semantic field')
        result.append({'id':key,'entity':entity,'presentation':presentation,
                       'primary':primary,'summary':secondary,'actions':spec.get('actions',[]),
                       'implemented_in_live_app':spec.get('status')=='existing'})
    return result


def rendered_rows(design,width):
    items=[]
    for row in catalog(design,width):
        fields=[]
        for name in row['primary']+row['summary']:
            safe=html.escape(str(name),quote=True)
            fields.append(f'<span class="semantic-field" data-field="{safe}">{safe}: <i>fixture</i></span>')
        actions=' '.join(html.escape(a) for a in row['actions'])
        items.append('<article class="frob-semantic-row" data-row="'+html.escape(row['id'],quote=True)+
                     '" data-entity="'+html.escape(row['entity'],quote=True)+
                     '" data-mode="'+row['presentation']+'" tabindex="0" role="group" aria-label="'+
                     html.escape(row['entity'],quote=True)+'">'+
                     '<strong>'+html.escape(row['entity'])+'</strong><div class="semantic-fields">'+
                     ''.join(fields)+'</div><small>Available intents: '+actions+'</small></article>')
    return '<div class="semantic-specimens" aria-label="Read-only semantic row specimens">'+''.join(items)+'</div>'
