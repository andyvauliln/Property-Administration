from django import template

register = template.Library()

@register.filter(name='split_underscore')
def split_underscore(value):
    return value.replace('_', ' ')

@register.filter
def get_item(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    else:
        return getattr(obj, key, None)

@register.filter(name='split')
def split(value, key):
  return value.split(key)