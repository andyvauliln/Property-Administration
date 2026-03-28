"""
Audit logging for QuerySet.update(), which bypasses model save() and post_save signals.
"""
from mysite.signals import (
    _values_equal,
    get_current_user_info,
    serialize_value,
    should_track_model,
)


def _field_values_from_instance(obj, field_names):
    return {f: serialize_value(getattr(obj, f)) for f in field_names}


def audit_queryset_update(queryset, *, changed_by=None, **kwargs):
    """
    Run queryset.update(**kwargs) and append AuditLog rows for each affected object,
    matching the shape produced by mysite.signals post_save updates.
    """
    if not kwargs:
        raise ValueError("audit_queryset_update requires at least one keyword field")

    model = queryset.model
    if not should_track_model(model()):
        return queryset.update(**kwargs)

    from mysite.models import AuditLog

    fields = list(kwargs.keys())
    pks = list(queryset.values_list("pk", flat=True))
    if not pks:
        return 0

    old_rows = {}
    for obj in model.objects.filter(pk__in=pks):
        old_rows[obj.pk] = _field_values_from_instance(obj, fields)

    rows_updated = queryset.update(**kwargs)

    by = changed_by if changed_by is not None else get_current_user_info()

    after_by_pk = {o.pk: o for o in model.objects.filter(pk__in=pks)}

    for pk in pks:
        obj = after_by_pk.get(pk)
        if obj is None:
            continue
        new_subset = _field_values_from_instance(obj, fields)
        old_subset = old_rows.get(pk, {})
        changed_fields = [
            f
            for f in fields
            if not _values_equal(old_subset.get(f), new_subset.get(f))
        ]
        if not changed_fields:
            continue
        AuditLog.objects.create(
            model_name=model.__name__,
            object_id=str(pk),
            object_repr=str(obj),
            action="update",
            changed_by=by,
            old_values={f: old_subset.get(f) for f in changed_fields},
            new_values={f: new_subset.get(f) for f in changed_fields},
            changed_fields=changed_fields,
        )

    return rows_updated
