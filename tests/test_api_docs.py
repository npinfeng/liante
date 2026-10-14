"""Bidirectional inventory, Markdown, OpenAPI and permission validation."""
import re
from pathlib import Path
import yaml
from tools.list_api_routes import collect
from test_system_engineering import system

def test_openapi_actual_routes_both_directions(system):
    _,app,_,_=system
    root=Path(__file__).resolve().parents[1]
    spec=yaml.safe_load((root/'docs/openapi.yaml').read_text(encoding='utf-8'))
    rows=collect(app)
    actual={(row['method'].lower(),row['path']) for row in rows}
    documented={(method,path) for path,operations in spec['paths'].items() for method in operations if method in {'get','post','put','patch','delete','head','options'}}
    assert actual==documented
    ids=[]
    for row in rows:
        operation=spec['paths'][row['path']][row['method'].lower()]
        ids.append(operation['operationId'])
        assert operation['x-roles']==row['auth']
        assert bool(operation.get('security'))==(row['auth']!=['public'])
    assert len(ids)==len(set(ids))
    md=(root/'docs/API.md').read_text(encoding='utf-8')
    sections=re.findall(r'^### .+ — `(GET|POST|PUT|PATCH|DELETE) ([^`]+)`',md,re.M)
    assert {(method.lower(),path) for method,path in sections}==actual
    assert len(sections)==len(rows)
    assert '真实 Stage3 模型 NOT CONFIGURED' in md

def test_openapi_schema_refs_resolve():
    root=Path(__file__).resolve().parents[1]
    spec=yaml.safe_load((root/'docs/openapi.yaml').read_text(encoding='utf-8'))
    def walk(value):
        if isinstance(value,dict):
            if '$ref' in value:
                node=spec
                for part in value['$ref'].removeprefix('#/').split('/'):node=node[part]
            for node in value.values():walk(node)
        elif isinstance(value,list):
            for node in value:walk(node)
    walk(spec)
    assert spec['components']['schemas']['Measurement']['properties']['unit']['maxLength']==32
