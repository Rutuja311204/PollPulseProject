"""Template filters used across PollPulse templates."""
from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """{{ mydict|get_item:key }} — dictionary lookup with a variable key."""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def at(sequence, index):
    """{{ mylist|at:i }} — list item by position (None if out of range)."""
    try:
        return sequence[int(index)]
    except (IndexError, TypeError, ValueError):
        return None


@register.filter
def contains(container, item):
    try:
        return item in (container or [])
    except TypeError:
        return False


@register.filter
def duration(seconds):
    """83 -> '1 min 23 s'."""
    if seconds in (None, ""):
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} h {minutes} min"
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


@register.filter
def initial(user):
    name = (getattr(user, "first_name", "") or getattr(user, "username", "") or "?").strip()
    return name[:1].upper()


@register.simple_tag
def query_replace(request, **kwargs):
    """Rebuild the current query string with some values replaced (pagination links)."""
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()
