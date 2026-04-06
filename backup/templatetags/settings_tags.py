import datetime

from django import template

register = template.Library()

_DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


@register.filter
def default_label(value, default):
    """Return '(default)' when value matches default, or '(default: X)' when it differs."""
    if default is None:
        return ""

    # Normalise for comparison: TimeField stores time objects but default may be a string
    cmp_value = value
    cmp_default = default
    if isinstance(value, datetime.time) and isinstance(default, str):
        try:
            cmp_default = datetime.time.fromisoformat(default)
        except (ValueError, TypeError):
            pass

    if cmp_value == cmp_default:
        return "(default)"

    # Format the default for display
    if isinstance(default, bool):
        display = "Yes" if default else "No"
    elif isinstance(default, datetime.time):
        display = default.strftime("%H:%M")
    elif isinstance(default, str) and ":" in default and len(default) == 5:
        # Time-like string e.g. "03:00"
        display = default
    else:
        display = str(default)

    return f"(default: {display})"


@register.filter
def day_name(value):
    """Convert day-of-week integer (0=Monday) to name."""
    try:
        return _DAY_NAMES[int(value)]
    except (IndexError, ValueError, TypeError):
        return str(value)
