from django import template
import calendar
import json
from datetime import timedelta
from datetime import datetime
import locale

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

@register.filter(name='range')
def filter_range(start, end):
    return range(start, int(end) + 1)

@register.filter
def get_dic_item(dictionary, key):
    # Try to get the value using the key as is (likely an integer)
    result = dictionary.get(key)
    
    # If that doesn't work, try converting the key to a string
    if result is None:
        result = dictionary.get(str(key))
    
    # If that still doesn't work, try converting the key to an integer
    if result is None:
        try:
            result = dictionary.get(int(key))
        except ValueError:
            pass
    
    return result

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


@register.filter(name='second_to_last')
def second_to_last(value):
    try:
        return value.split('.')[-2]
    except IndexError:
        return value


@register.filter(name='subtract')
def subtract(value, arg):
    return value - arg


@register.filter(name='add')
def add(value, arg):
    return value + arg


@register.filter(name='get_item2')
def get_item2(dictionary, key):
    return dictionary.get(key, None)


@register.filter(name='format_number')
def format_number(value):
    try:
        return "{:,.0f}".format(value)
    except (ValueError, TypeError):
        return value


@register.filter
def format_nullable_date(date_string):
    if date_string is None:
        return '-'

    try:
        formatted_date = date_string.strftime('%B %d %Y')
    except AttributeError:
        return '-'

    return formatted_date

@register.filter
def filter_by_id(apartments, apartment_id):
    """Find an apartment in a list by its ID"""
    if not apartments:
        return []
    return [apt for apt in apartments if apt.get('id') == apartment_id]
