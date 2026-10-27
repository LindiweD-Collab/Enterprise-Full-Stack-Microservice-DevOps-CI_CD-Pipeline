import django_filters

from ticketing.models import Event


class EventFilter(django_filters.FilterSet):
    starts_after = django_filters.IsoDateTimeFilter(
        field_name="starts_at", lookup_expr="gte"
    )
    starts_before = django_filters.IsoDateTimeFilter(
        field_name="starts_at", lookup_expr="lte"
    )
    city = django_filters.CharFilter(field_name="venue__city", lookup_expr="iexact")

    class Meta:
        model = Event
        fields = ["venue", "status"]
