from django import template

register = template.Library()

@register.filter(name='split_underscore')
def split_underscore(value):
    if isinstance(value, str):
        return value.replace('_', ' ')
    return value

@register.filter(name='get_field_name')
def get_field_name(value):
    if not value:
        return ""
    parts = value.split('.')
    return parts[-2] if len(parts) > 1 else parts[0]

@register.filter(name='get_type')
def get_type(special_fields, field):
    return special_fields.get(field, {}).get('type', '')

@register.filter(name='get_options')
def get_options(special_fields, field):
    return special_fields.get(field, {}).get('options', [])

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

@register.filter(name='get_attr')
def get_attr(obj, attr_name):
    return getattr(obj, attr_name, '')


EXCLUDED_FIELDS = ["is_active", "is_staff", "password", "updated_at", "created_at", "is_superuser", "last_login"]

@register.filter(name='is_excluded')
def is_excluded(value):
    return value in EXCLUDED_FIELDS


@register.simple_tag
def render_field(item, field):
    parts = field.split('.')
    value = item
    for part in parts:
        value = getattr(value, part, None)
        if value is None:
            break
    return value