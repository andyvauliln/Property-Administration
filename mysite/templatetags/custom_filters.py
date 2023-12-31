from django import template
import calendar
import json


register = template.Library()

@register.filter(name='split_underscore')
def split_underscore(value):
    if isinstance(value, str):
        return value.replace('_', ' ')
    return value

@register.filter
def get_item(obj, key):
    if isinstance(obj, dict):
        fields_data = obj.get('fields', {})
        return fields_data.get(key, obj.get(key, ""))
    else:
        value = getattr(obj, key, None)
        return value if value is not None else ""
    
@register.filter
def get_custom_item(obj, attr_string):
    attrs = attr_string.split('.')
    
    for attr in attrs:
        if isinstance(obj, dict):
            obj = obj.get(attr)
        else:
            obj = getattr(obj, attr, None)
        if obj is None:
            return ""
    return obj    

@register.filter(name='split')
def split(value, key):
  if isinstance(value, str):
        return value.split(key)
  return value


@register.filter(name='display_query')
def display_query(value):
    return value.replace('+', ' AND ').replace('|', ' OR ')

