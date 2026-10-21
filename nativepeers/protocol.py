"""Small validation for the checked-in v1 JSON contracts."""
from .errors import require


def valid(value,schema):
    if 'enum' in schema and value not in schema['enum']:return False
    if 'const' in schema and value!=schema['const']:return False
    kind=schema.get('type')
    if kind=='string':return isinstance(value,str) and schema.get('minLength',0)<=len(value)<=schema.get('maxLength',len(value))
    if kind=='boolean':return type(value) is bool
    if kind in ('integer','number'):
        return (type(value) is int if kind=='integer' else type(value) in (int,float)) and schema.get('minimum',value)<=value<=schema.get('maximum',value)
    if kind=='array':return isinstance(value,list) and schema.get('minItems',0)<=len(value)<=schema.get('maxItems',len(value)) and all(valid(v,schema.get('items',{})) for v in value)
    if kind=='object':
        return isinstance(value,dict) and set(schema.get('required',[]))<=set(value) and (schema.get('additionalProperties',True) or not set(value)-set(schema.get('properties',{}))) and all(valid(v,schema.get('properties',{}).get(k,{})) for k,v in value.items())
    return True


def validate(value,schema):require(valid(value,schema),'PROTOCOL_ERROR','Input does not match the NativePeers v1 contract')
