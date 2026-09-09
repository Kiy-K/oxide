from pkg.models import Record, DEFAULT_LIMIT


def build(limit=DEFAULT_LIMIT):
    def inner(x):
        return Record(x)

    return inner


@register("svc")
def run():
    b = build()
    return b(1)
