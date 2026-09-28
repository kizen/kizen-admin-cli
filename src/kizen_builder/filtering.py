"""Filter-building DSL for Kizen's query format.

Build filters as Python expressions and serialize them to the wire shapes
accepted by records search, dashlets, and automation condition steps::

    with filter_context("patients"):
        expr = All(
            Field("first_name") == "Jack",
            Field("risk_level") in Options("High", "Critical"),
        )
        body = expr.as_dict()          # records-search body shape
        cfg = as_filter_config(expr)   # condition-step filter_config shape

Field/option/tag names are resolved to UUIDs against the env's schema via a
client that implements the small lookup surface of
:class:`kizen_builder.api.schema.SchemaClient`. Inside ``filter_context`` the
client rides thread-local state so operator overloading (``==``, ``in``)
works; pass ``client=`` explicitly in tests.

Ported from the internal ``kznclient`` library (kizen/filtering.py) — the
UI-parity tests in tests/test_*_parity.py are the captured spec for the
payload shapes this module emits.
"""

import contextlib
import threading
from datetime import UTC, date, datetime
from uuid import UUID

_local_filter_cx = threading.local()
_default_client = None

DEFAULT_FIELDS = {"name", "email", "first_name", "last_name", "created", "stage"}


def get_default_client():
    """Return the process-wide schema client, creating one from the
    resolved profile's credentials on first use."""
    global _default_client
    if _default_client is None:
        from kizen_builder.api.schema import SchemaClient

        _default_client = SchemaClient.from_env()
    return _default_client


def set_default_client(client):
    """Install a schema client (or None to reset). Tests use this to inject
    an offline stub."""
    global _default_client
    _default_client = client


@contextlib.contextmanager
def filter_context(obj_id, client=None):
    prev_client = getattr(_local_filter_cx, "client", None)
    prev_obj_id = getattr(_local_filter_cx, "obj_id", None)
    if client is None:
        client = get_default_client()

    try:
        UUID(obj_id)
        is_uuid = True
    except ValueError:
        is_uuid = False
    # Resolved outside the except block so a LookupError isn't chained to
    # the UUID parse failure.
    if not is_uuid:
        obj_id = client.custom_object(obj_id)["id"]

    _local_filter_cx.client = client
    _local_filter_cx.obj_id = obj_id
    try:
        yield
    finally:
        _local_filter_cx.client = prev_client
        _local_filter_cx.obj_id = prev_obj_id


def get_cx_client():
    if client := getattr(_local_filter_cx, "client", None):
        return client
    return get_default_client()


def get_cx_obj_id():
    return getattr(_local_filter_cx, "obj_id", None)


def _format_date_value(value):
    """
    Format date/datetime values the way the Kizen UI does:
    - datetime → ISO-8601 UTC with milliseconds, e.g. "2024-12-31T06:00:00.000Z"
    - date → "YYYY-MM-DD"
    - anything else passes through unchanged
    (datetime must be checked first; it is a subclass of date)
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return (
            value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
        )
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


# Field types whose values are option uuids and whose =/!= take a BARE uuid
# (timezone options use the IANA name as both name and id, e.g. "America/Chicago")
_SINGLE_SELECT_TYPES = {"dropdown", "radio", "status", "yesnomaybe", "timezone"}

# Field types with has/!has/has_any/!has_any semantics and list values
_MULTI_OPTION_TYPES = {"checkboxes", "dynamictags"}

_HAS_CONDITION_MAP = {
    "contains": "has",
    "!contains": "!has",
    "=": "has",
    "!=": "!has",
    "is_any_of": "has_any",
    "is_none_of": "!has_any",
}

_LIST_VALUE_CONDITIONS = {"has", "!has", "has_any", "has_all", "has_none", "!has_any"}

# ---------------------------------------------------------------------------
# Which conditions each field type supports in the Kizen UI. Conditions are
# the DSL-level tokens produced by the Field methods (before the field-type-
# specific mapping in FilterCondition.as_dict). Anything outside these sets
# cannot be built in the UI and raises an informative error.
# (See docs/ui_filter_capture.md for the captured condition tables.)
# ---------------------------------------------------------------------------

_TEXT_CONDITIONS = {
    "=",
    "!=",
    "contains",
    "!contains",
    "starts_with",
    "ends_with",
    "!starts_with",
    "!ends_with",
    "is_blank",
}
_NUMBER_CONDITIONS = {"=", "!=", "<", "<=", ">", ">=", "between", "is_blank"}
_DATE_CONDITIONS = {
    "=",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "between",
    "month_equals",
    "is_blank",
}
_SINGLE_SELECT_CONDITIONS = {"=", "!=", "is_any_of", "is_none_of", "is_blank"}
_MULTI_OPTION_CONDITIONS = {
    "=",
    "!=",
    "contains",
    "!contains",
    "is_any_of",
    "is_none_of",
    "has_any",
    "is_blank",
}

_UI_SUPPORTED_CONDITIONS = {
    "text": _TEXT_CONDITIONS,
    "email": _TEXT_CONDITIONS,
    "longtext": {"is_blank"},
    "phonenumber": {"=", "contains", "is_blank"},
    "checkbox": {"="},
    "checkboxes": _MULTI_OPTION_CONDITIONS,
    "dynamictags": _MULTI_OPTION_CONDITIONS,
    "dropdown": _SINGLE_SELECT_CONDITIONS,
    "radio": _SINGLE_SELECT_CONDITIONS,
    "status": _SINGLE_SELECT_CONDITIONS,
    "yesnomaybe": _SINGLE_SELECT_CONDITIONS,
    "timezone": {"=", "!=", "is_blank"},
    "rating": {"=", "!=", "<", "<=", ">", ">=", "is_any_of", "is_none_of", "is_blank"},
    "integer": _NUMBER_CONDITIONS,
    "decimal": _NUMBER_CONDITIONS,
    "money": _NUMBER_CONDITIONS,
    "files": {">", ">=", "<", "is_blank"},
    "date": _DATE_CONDITIONS,
    "datetime": _DATE_CONDITIONS,
    "team_selector": {"=", "!=", "is_any_of", "is_none_of", "is_me", "is_blank"},
    "relationship": {"contains", "!contains", "is_any_of", "is_none_of", "is_blank"},
}

# Default fields whose UI condition list is more restrictive than their
# field type's (e.g. no Is Blank / Isn't Blank options).
_DEFAULT_FIELD_CONDITION_OVERRIDES = {
    "created": _DATE_CONDITIONS - {"is_blank"},
    "updated": _DATE_CONDITIONS - {"is_blank"},
    "owner": {"=", "!=", "is_any_of", "is_none_of", "is_me"},
    "email_status": _SINGLE_SELECT_CONDITIONS - {"is_blank"},
}

_STAGE_CONDITIONS = {
    "=",
    "!=",
    "is_any_of",
    "is_none_of",
    "time_in_stage",
    "entered_stage",
    "left_stage",
}


def _check_ui_supported(field, condition):
    field_type = field["field_type"]
    if field["is_default"] and field["name"] == "stage":
        allowed = _STAGE_CONDITIONS
    elif field["is_default"] and field["name"] in _DEFAULT_FIELD_CONDITION_OVERRIDES:
        allowed = _DEFAULT_FIELD_CONDITION_OVERRIDES[field["name"]]
    else:
        allowed = _UI_SUPPORTED_CONDITIONS.get(field_type)
    if allowed is not None and condition not in allowed:
        raise ValueError(
            f"The Kizen UI does not support condition {condition!r} on field "
            f"{field['name']!r} (field type {field_type!r}). "
            f"Supported conditions: {', '.join(sorted(allowed))}. "
            f"(is_blank covers both is_blank() and not_blank())"
        )


class FilterCondition:
    def __init__(self, field, condition, value, extra=None):
        self.field = field
        self.condition = condition
        self.value = value
        self.extra = extra

    def as_dict(self, parent=None):
        client = get_cx_client()
        obj_id = get_cx_obj_id()
        field = client.get_field(obj_id, self.field)

        if field is None:
            raise ValueError(
                f"Unknown field {self.field!r} on object {obj_id!r}. "
                f"Use the field's api name (or its uuid)."
            )

        _check_ui_supported(field, self.condition)

        field_type = field["field_type"]
        condition = self.condition
        value = self.value
        extra = dict(self.extra or {})

        if isinstance(value, tuple):
            value = list(value)

        # Translate an option name to its uuid; values that aren't option
        # names (e.g. already a uuid) pass through unchanged.
        name_lookup = {
            opt["name"].lower(): opt["id"] for opt in (field.get("options") or [])
        }

        def opt_id(v):
            if isinstance(v, str):
                return name_lookup.get(v.lower(), v)
            return v

        def tag_id(v):
            """
            dynamictags options live in a separate endpoint (field metadata has
            no "options"); translate tag names to ids via the API. Values that
            are already uuids pass through unchanged.
            """
            if not isinstance(v, str):
                return v
            try:
                UUID(v)
                return v
            except ValueError:
                pass
            for tag in client.get_field_tags(obj_id, field["id"], v):
                if tag["name"].lower() == v.lower():
                    return tag["id"]
            return v

        # "Is Me" is expressed by the API as `= "is_me"`
        if condition == "is_me":
            condition = "="
            value = "is_me"

        # --- stage is a default field with a special payload shape ---------
        if field["is_default"] and self.field == "stage":
            if isinstance(value, list):
                value = [opt_id(v) for v in value]
            else:
                value = opt_id(value)
            return {
                "type": "fields_v2",
                "subtype": "non_custom",
                "field": "stage",
                "overwrite": {"type": "stage"},
                "condition": condition,
                "value": value,
                **extra,
            }

        if field["is_default"]:
            field_identifier = self.field
            type = "fields"
            subtype = "non_custom"
        else:
            field_identifier = f'"custom"::{field["id"]}'
            type = "fields_v2"
            subtype = "custom"

        # --- field-type-specific condition/value handling -------------------
        if field_type in _MULTI_OPTION_TYPES:
            if condition == "=" and value == []:
                condition, value = "is_blank", True
            elif condition == "!=" and value == []:
                condition, value = "is_blank", False
            elif condition in _HAS_CONDITION_MAP:
                condition = _HAS_CONDITION_MAP[condition]
            if condition in _LIST_VALUE_CONDITIONS:
                if not isinstance(value, list):
                    value = [value]
                translate = tag_id if field_type == "dynamictags" else opt_id
                value = [translate(v) for v in value]

        elif field_type in _SINGLE_SELECT_TYPES:
            # =/!= take a bare option uuid; any-of conditions take a list.
            # Contacts' default email_status field is special: the API takes
            # snake_case slugs of the option names ("Opted In" -> "opted_in"),
            # not option uuids.
            if field["is_default"] and self.field == "email_status":

                def translate(v):
                    return v.lower().replace(" ", "_") if isinstance(v, str) else v
            else:
                translate = opt_id
            if isinstance(value, list):
                value = [translate(v) for v in value]
            elif condition in {"=", "!="}:
                value = translate(value)

        elif field_type == "rating":
            # rating values are the numbers as strings, NOT option uuids
            if isinstance(value, list):
                value = [str(v) for v in value]
            elif condition != "is_blank":
                value = str(value)

        elif field_type == "relationship":
            # contains/!contains keep their tokens but take a list of record uuids
            if condition in {"contains", "!contains"} and not isinstance(value, list):
                value = [value]

        elif field_type in {"date", "datetime"}:
            if isinstance(value, list):
                value = [_format_date_value(v) for v in value]
            else:
                value = _format_date_value(value)

        elif field_type == "checkbox":
            if condition == "=":
                value = value in {"yes", True, "checked"}

        return {
            "type": type,
            "subtype": subtype,
            "field": field_identifier,
            "condition": condition,
            "value": value,
            **extra,
        }


class All:
    _group_and = True

    def __init__(self, *children):
        # Process children and convert boolean results back to FilterConditions
        processed_children = []

        num_contains_children = sum(1 for c in children if isinstance(c, bool))
        result_stack_start_index = 0
        if hasattr(_local_filter_cx, "in_result_stack"):
            result_stack_start_index = (
                len(_local_filter_cx.in_result_stack) - num_contains_children
            )

        for child in children:
            if (
                isinstance(child, bool)
                and hasattr(_local_filter_cx, "in_result_stack")
                and _local_filter_cx.in_result_stack
            ):
                condition = _local_filter_cx.in_result_stack.pop(
                    result_stack_start_index
                )
                # Check if this was a 'not in' operation (child is False)
                if child is False and condition.condition == "is_any_of":
                    condition.condition = "is_none_of"
                processed_children.append(condition)
            else:
                processed_children.append(child)
        self.children = processed_children

    def as_dict(self, parent=None):
        any_children_are_groups = any(
            isinstance(child, (Any, All)) for child in self.children
        )

        if any_children_are_groups:
            # Separate group children from filter children
            filters = [c for c in self.children if not isinstance(c, (Any, All))]
            groups = [c for c in self.children if isinstance(c, (Any, All))]

            # Create the result
            children = []
            if filters:
                # Combine all non-group filters into one group
                children.append(self.__class__(*filters))
            children.extend(groups)
        else:
            children = self.children

        children_key = "query" if any_children_are_groups else "filters"
        result = {
            "and": self._group_and,
            children_key: [child.as_dict(parent=self) for child in children],
        }

        # kizen expects the outermost JSON object to contain "query"
        if not parent and "filters" in result:
            result = {"and": self._group_and, "query": [result]}

        return result


class Any(All):
    _group_and = False


class Field:
    """
    This class should be as simple as possible. We want to capture all logic and transformation
    in FilterCondition (and in particular, most in FilterCondition.as_dict(), which is called
    after the filter is fully constructed).
    """

    def __init__(self, field):
        self.field_apiname = field

    def __eq__(self, other):
        return self.equals(other)

    def __ne__(self, other):
        return self.not_equals(other)

    def __lt__(self, other):
        return FilterCondition(self.field_apiname, "<", other)

    def __le__(self, other):
        return FilterCondition(self.field_apiname, "<=", other)

    def __gt__(self, other):
        return FilterCondition(self.field_apiname, ">", other)

    def __ge__(self, other):
        return FilterCondition(self.field_apiname, ">=", other)

    def __contains__(self, item):
        condition = self.contains(item)
        # Store in thread-local storage using a stack to handle multiple operations
        if not hasattr(_local_filter_cx, "in_result_stack"):
            _local_filter_cx.in_result_stack = []
        _local_filter_cx.in_result_stack.append(condition)
        return True

    def is_checked(self):
        return self.equals(True)

    def not_checked(self):
        return self.equals(False)

    def equals(self, val):
        return FilterCondition(self.field_apiname, "=", val)

    def not_equals(self, val):
        return FilterCondition(self.field_apiname, "!=", val)

    def contains(self, val):
        return FilterCondition(self.field_apiname, "contains", val)

    def not_contains(self, val):
        return FilterCondition(self.field_apiname, "!contains", val)

    def startswith(self, val):
        return FilterCondition(self.field_apiname, "starts_with", val)

    def endswith(self, val):
        return FilterCondition(self.field_apiname, "ends_with", val)

    def not_startswith(self, val):
        return FilterCondition(self.field_apiname, "!starts_with", val)

    def not_endswith(self, val):
        return FilterCondition(self.field_apiname, "!ends_with", val)

    def not_blank(self):
        return FilterCondition(self.field_apiname, "is_blank", False)

    def is_blank(self):
        return FilterCondition(self.field_apiname, "is_blank", True)

    def month_equals(self, month):
        """month is 1-12 (January=1)"""
        return FilterCondition(self.field_apiname, "month_equals", month)

    def earlier_than(self, val):
        return FilterCondition(self.field_apiname, "<", val)

    def earlier_than_or_on(self, val):
        return FilterCondition(self.field_apiname, "<=", val)

    def later_than(self, val):
        return FilterCondition(self.field_apiname, ">", val)

    def later_than_or_on(self, val):
        return FilterCondition(self.field_apiname, ">=", val)

    def between(self, start, end):
        return FilterCondition(self.field_apiname, "between", [start, end])

    def is_any_of(self, *values):
        return FilterCondition(self.field_apiname, "is_any_of", values)

    def not_any_of(self, *values):
        return FilterCondition(self.field_apiname, "is_none_of", values)

    def is_me(self):
        return FilterCondition(self.field_apiname, "is_me", None)

    def has_any(self, *values):
        return FilterCondition(self.field_apiname, "has_any", values)

    def has_all(self, *values):
        return FilterCondition(self.field_apiname, "has_all", values)

    def has_none(self, *values):
        return FilterCondition(self.field_apiname, "has_none", values)

    # --- stage-specific conditions ------------------------------------------

    _STAGE_COMPARISONS = {
        "on": "=",
        "not_on": "!=",
        "earlier_than": "<",
        "earlier_than_or_on": "<=",
        "later_than": ">",
        "later_than_or_on": ">=",
    }

    def time_in_stage(self, stage, more_than=None, less_than=None, units="days"):
        if (more_than is None) == (less_than is None):
            raise ValueError("pass exactly one of more_than= or less_than=")
        if more_than is not None:
            comparison_condition, amount = ">", more_than
        else:
            comparison_condition, amount = "<", less_than
        return FilterCondition(
            self.field_apiname,
            "time_in_stage",
            stage,
            extra={
                "comparison_condition": comparison_condition,
                "comparison_value": str(amount),
                "comparison_type": units,
            },
        )

    def _stage_date_condition(self, condition, stage, kwargs):
        if len(kwargs) != 1 or next(iter(kwargs)) not in self._STAGE_COMPARISONS:
            raise ValueError(
                f"pass exactly one of: {', '.join(self._STAGE_COMPARISONS)}=<date>"
            )
        kw, val = next(iter(kwargs.items()))
        if isinstance(val, datetime):
            val = val.date()
        if isinstance(val, date):
            val = val.strftime("%Y-%m-%d")
        return FilterCondition(
            self.field_apiname,
            condition,
            stage,
            extra={
                "comparison_condition": self._STAGE_COMPARISONS[kw],
                "comparison_value": val,
            },
        )

    def entered_stage(self, stage, **kwargs):
        return self._stage_date_condition("entered_stage", stage, kwargs)

    def left_stage(self, stage, **kwargs):
        return self._stage_date_condition("left_stage", stage, kwargs)


# ---------------------------------------------------------------------------
# Contact-specific (non-field) filters
#
# The Contacts list UI offers filter categories beyond "Fields" that POST
# entirely different filter shapes. See docs/ui_filter_capture.md.
# ---------------------------------------------------------------------------


def _is_uuid(value):
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class _TagsFilter:
    def __init__(self, condition, tags=()):
        self.condition = condition
        self.tags = tags

    def as_dict(self, parent=None):
        d = {"type": "tags", "subtype": "tag", "condition": self.condition}
        if self.condition not in {"is_blank", "is_not_blank"}:
            client = get_cx_client()
            d["ids"] = [self._tag_id(client, t) for t in self.tags]
        return d

    @staticmethod
    def _tag_id(client, value):
        if _is_uuid(value):
            return value
        for tag in client.get_contact_tags(value):
            if tag["name"].lower() == value.lower():
                return tag["id"]
        return value


class Tags:
    """Contact "Tags" filter category (contacts only)."""

    @staticmethod
    def has(tag):
        return _TagsFilter("has", (tag,))

    @staticmethod
    def has_not(tag):
        return _TagsFilter("has_not", (tag,))

    @staticmethod
    def any_of(*tags):
        return _TagsFilter("has_any", tags)

    @staticmethod
    def none_of(*tags):
        return _TagsFilter("has_none", tags)

    @staticmethod
    def is_blank():
        return _TagsFilter("is_blank")

    @staticmethod
    def not_blank():
        return _TagsFilter("is_not_blank")


class _SubscriptionListFilter:
    def __init__(self, status, lists):
        self.status = status
        self.lists = lists

    def as_dict(self, parent=None):
        client = get_cx_client()
        return {
            "type": "subscription_lists",
            "subtype": "subscription_list",
            "status": self.status,
            "subscription_list_ids": [
                self._list_id(client, item) for item in self.lists
            ],
        }

    @staticmethod
    def _list_id(client, value):
        if _is_uuid(value):
            return value
        for sub_list in client.get_subscription_lists():
            if sub_list["name"].lower() == value.lower():
                return sub_list["id"]
        return value


class SubscriptionLists:
    """Contact "Subscription Lists" filter category (contacts only)."""

    @staticmethod
    def is_opted_in(subscription_list):
        return _SubscriptionListFilter("is_opted_in", (subscription_list,))

    @staticmethod
    def is_not_opted_in(subscription_list):
        return _SubscriptionListFilter("is_not_opted_in", (subscription_list,))

    @staticmethod
    def is_opted_out_of(subscription_list):
        return _SubscriptionListFilter("is_opted_out_of", (subscription_list,))

    @staticmethod
    def opted_in_to_any_of(*subscription_lists):
        return _SubscriptionListFilter("opted_in_to_any_of", subscription_lists)

    @staticmethod
    def opted_in_to_none_of(*subscription_lists):
        return _SubscriptionListFilter("opted_in_to_none_of", subscription_lists)


class _MessageFilter:
    def __init__(self, operator, event, last_n_days=None):
        self.operator = operator
        self.event = event
        self.last_n_days = last_n_days

    def as_dict(self, parent=None):
        return {
            "type": "library_messages",
            "subtype": "sent_messages",
            "operator": self.operator,
            "event": self.event,
            "last_n_days": str(self.last_n_days)
            if self.last_n_days is not None
            else None,
        }


def _message_method(operator, event):
    @staticmethod
    def method(last_n_days=None):
        return _MessageFilter(operator, event, last_n_days)

    return method


class Messages:
    """Contact "Messages" filter category (contacts only)."""

    sent = _message_method("has_matches", "sent")
    not_sent = _message_method("has_no_matches", "sent")
    delivered = _message_method("has_matches", "delivered")
    not_delivered = _message_method("has_no_matches", "delivered")
    opened = _message_method("has_matches", "opened")
    didnt_open = _message_method("has_no_matches", "opened")
    clicked_link = _message_method("has_matches", "clicked")
    didnt_click_link = _message_method("has_no_matches", "clicked")
    opened_attachment = _message_method("has_matches", "attachment_opened")
    didnt_open_attachment = _message_method("has_no_matches", "attachment_opened")
    unsubscribed = _message_method("has_matches", "unsubscribed")
    complained = _message_method("has_matches", "complained")
    bounced = _message_method("has_matches", "bounced")


class _InteractionFilter:
    def __init__(self, type_condition, url_condition=None, url_value=None):
        self.type_condition = type_condition  # has_any / has_none
        self.url_condition = url_condition  # equals / starts_with / contains
        self.url_value = url_value

    def as_dict(self, parent=None):
        d = {
            "type": "interactions",
            "subtype": "interactions",
            "occurrence_condition": "atleast_one",
            "interaction_type_condition": self.type_condition,
            "payload": {"occurrence_condition": {"type": self.type_condition}},
            "data_condition": "any",
        }
        if self.url_condition is not None:
            d["payload"]["field_conditions"] = {
                "kznjs_url": {"condition": self.url_condition, "value": self.url_value}
            }
            d["data_condition"] = self.url_condition
            d["timeframe_condition"] = "any"
            d["data_value"] = self.url_value
        return d


def _url_kwargs_to_condition(kwargs):
    conditions = {"equals", "starts_with", "contains"}
    if not kwargs:
        return None, None
    if len(kwargs) != 1 or next(iter(kwargs)) not in conditions:
        raise ValueError(f"pass at most one of: {', '.join(sorted(conditions))}=<url>")
    return next(iter(kwargs.items()))


class Interactions:
    """Contact "Interactions" filter category (contacts only)."""

    @staticmethod
    def has_interaction_at_url(**kwargs):
        """Interactions.has_interaction_at_url(contains="example.com") or no kwargs for any URL"""
        condition, value = _url_kwargs_to_condition(kwargs)
        return _InteractionFilter("has_any", condition, value)

    @staticmethod
    def no_interaction_at_url(**kwargs):
        condition, value = _url_kwargs_to_condition(kwargs)
        return _InteractionFilter("has_none", condition, value)


# ---------------------------------------------------------------------------
# Category filters available on all objects (custom objects + contacts)
# ---------------------------------------------------------------------------


_UTM_TYPES = {"source", "campaign", "medium", "term", "content"}


class _LeadSourceFilter:
    def __init__(
        self,
        condition_type,
        condition_value,
        source_type,
        source=None,
        utm_type="source",
    ):
        self.condition_type = condition_type
        self.condition_value = condition_value
        self.source_type = source_type
        self.source = source
        if utm_type not in _UTM_TYPES:
            raise ValueError(
                f"utm_type must be one of: {', '.join(sorted(_UTM_TYPES))}"
            )
        self.utm_type = utm_type

    def as_dict(self, parent=None):
        payload = {
            "condition_type": self.condition_type,
            "condition_value": self.condition_value,
            "source_type": self.source_type,
        }
        if self.source is not None:
            # for utm/custom sources the sub-condition is keyed by the UTM
            # component being matched (source/campaign/medium/term/content)
            payload[self.utm_type] = self.source
        return {"type": "lead_sources", "subtype": "lead_sources", "payload": payload}


def _source_condition(equals=None, no_value=False):
    """Build the utm/custom sub-condition dict."""
    if equals is not None:
        return {"condition": "equals", "value": equals}
    if no_value:
        return {"condition": "none"}
    return {"condition": "any"}


class LeadSources:
    """
    "Lead Sources" filter category.

    source_type is one of: direct_traffic, organic_search, site_referral,
    google_ads, social, utm, custom.

    For utm/custom sources, narrow by a UTM component with utm_type=
    ("source", "campaign", "medium", "term" or "content"; default "source")
    and equals=/no_value=:

        LeadSources.first_source_is("utm", equals="newsletter_link")
        LeadSources.first_source_is("utm", utm_type="campaign", no_value=True)
        LeadSources.first_source_is("custom", equals="partner_referral")
        LeadSources.last_source_is_not("utm")  # any UTM
    """

    @staticmethod
    def _make(condition_type, condition_value, source_type, utm_type, equals, no_value):
        source = None
        if source_type in {"utm", "custom"}:
            source = _source_condition(equals, no_value)
        elif equals is not None or no_value:
            raise ValueError(
                f"equals=/no_value= only apply to 'utm' and 'custom' sources, not {source_type!r}"
            )
        return _LeadSourceFilter(
            condition_type, condition_value, source_type, source, utm_type
        )

    @classmethod
    def first_source_is(
        cls, source_type, utm_type="source", equals=None, no_value=False
    ):
        return cls._make(
            "first_lead_source", True, source_type, utm_type, equals, no_value
        )

    @classmethod
    def first_source_is_not(
        cls, source_type, utm_type="source", equals=None, no_value=False
    ):
        return cls._make(
            "first_lead_source", False, source_type, utm_type, equals, no_value
        )

    @classmethod
    def last_source_is(
        cls, source_type, utm_type="source", equals=None, no_value=False
    ):
        return cls._make(
            "last_lead_source", True, source_type, utm_type, equals, no_value
        )

    @classmethod
    def last_source_is_not(
        cls, source_type, utm_type="source", equals=None, no_value=False
    ):
        return cls._make(
            "last_lead_source", False, source_type, utm_type, equals, no_value
        )

    @classmethod
    def any_source_is(cls, source_type, utm_type="source", equals=None, no_value=False):
        return cls._make(
            "any_lead_source", True, source_type, utm_type, equals, no_value
        )

    @classmethod
    def no_source_is(cls, source_type, utm_type="source", equals=None, no_value=False):
        return cls._make(
            "any_lead_source", False, source_type, utm_type, equals, no_value
        )


def _by_dict(by):
    if by is None:
        return {"team_member": None, "which": None}
    return {"team_member": True, "which": by}


class _LoggedActivityFilter:
    def __init__(
        self, submitted, activity_id=None, within_days=None, between=None, by=None
    ):
        self.submitted = submitted
        self.activity_id = activity_id
        self.within_days = within_days
        self.between = between
        self.by = by

    def as_dict(self, parent=None):
        d = {"type": "activities", "subtype": "activity"}
        if self.activity_id is None:
            d["activity_type"] = "any"
        else:
            d["activity_type"] = "specific"
            d["activity_object_id"] = self.activity_id
        payload = {"submitted": self.submitted}
        if self.within_days is not None:
            payload["time"] = {"past": True, "days": str(self.within_days)}
        elif self.between is not None:
            payload["time"] = {
                "date_range": [_format_date_value(v) for v in self.between]
            }
        elif self.submitted:
            payload["time"] = {"past": None, "days": None}
        payload["by"] = _by_dict(self.by)
        d["payload"] = payload
        return d


class LoggedActivities:
    """
    "Logged Activities" filter category. activity_id is the activity's uuid
    (Any Activity when omitted); by= is "is_me" or an employee uuid.
    """

    @staticmethod
    def submitted(activity_id=None, within_days=None, between=None, by=None):
        return _LoggedActivityFilter(True, activity_id, within_days, between, by)

    @staticmethod
    def not_submitted(activity_id=None, within_days=None, between=None, by=None):
        return _LoggedActivityFilter(False, activity_id, within_days, between, by)


class _ScheduledActivityFilter:
    def __init__(
        self,
        scheduled_condition,
        activity_id=None,
        n_days=None,
        date_range=None,
        assigned_to=None,
    ):
        self.scheduled_condition = scheduled_condition
        self.activity_id = activity_id
        self.n_days = n_days
        self.date_range = date_range
        self.assigned_to = assigned_to

    def as_dict(self, parent=None):
        d = {"type": "scheduled_activities", "subtype": "scheduled_activities"}
        if self.activity_id is None:
            d["activity_type"] = "any"
        else:
            d["activity_type"] = "specific"
            d["activity_object_id"] = self.activity_id
        d["scheduled_condition"] = self.scheduled_condition
        if self.n_days is not None:
            d["n_days"] = str(self.n_days)
        if self.date_range is not None:
            d["date_range"] = [_format_date_value(v) for v in self.date_range]
        if self.assigned_to is None:
            d["assigned_condition"] = "any_member"
        else:
            d["assigned_condition"] = "specific_member"
            d["assigned_id"] = self.assigned_to
        return d


class ScheduledActivities:
    """
    "Scheduled Activities" filter category. assigned_to= is "is_me" or an
    employee uuid; activity_id is an activity uuid (Any Activity when omitted).
    """

    @staticmethod
    def scheduled_any_time(activity_id=None, assigned_to=None):
        return _ScheduledActivityFilter(
            "any_time", activity_id, assigned_to=assigned_to
        )

    @staticmethod
    def not_scheduled(activity_id=None, assigned_to=None):
        return _ScheduledActivityFilter(
            "not_scheduled", activity_id, assigned_to=assigned_to
        )

    @staticmethod
    def scheduled_within_days(n_days, activity_id=None, assigned_to=None):
        return _ScheduledActivityFilter(
            "within_n_days", activity_id, n_days=n_days, assigned_to=assigned_to
        )

    @staticmethod
    def due_between(start, end, activity_id=None, assigned_to=None):
        return _ScheduledActivityFilter(
            "between", activity_id, date_range=(start, end), assigned_to=assigned_to
        )

    @staticmethod
    def overdue_more_than_days(n_days, activity_id=None, assigned_to=None):
        return _ScheduledActivityFilter(
            "overdue_more_than_n_days",
            activity_id,
            n_days=n_days,
            assigned_to=assigned_to,
        )


class _TeamInteractionFilter:
    def __init__(self, interacted, member=None, within_days=None):
        self.interacted = interacted
        self.member = member
        self.within_days = within_days

    def as_dict(self, parent=None):
        d = {"type": "association", "interacted": self.interacted, "subtype": "team"}
        if self.member is not None:
            d["with"] = self.member
        if self.within_days is None:
            d["time_past"] = "None_True"
        else:
            d["time_past"] = "True_True"
            d["past_n_days"] = str(self.within_days)
        return d


class TeamInteractions:
    """
    "Team Interactions" filter category. member= is "is_me" or an employee
    uuid (Any Team Member when omitted).
    """

    @staticmethod
    def interacted_with(member=None, within_days=None):
        return _TeamInteractionFilter(True, member, within_days)

    @staticmethod
    def not_interacted_with(member=None, within_days=None):
        return _TeamInteractionFilter(False, member, within_days)


class _AgenticWorkflowFilter:
    def __init__(self, status, time_period=None, workflow=...):
        self.status = status
        self.time_period = time_period
        self.workflow = workflow  # Ellipsis means "Any Agentic Workflow"

    def as_dict(self, parent=None):
        d = {"type": "automation2", "subtype": "automation"}
        if self.workflow is not ...:
            d["automation_id"] = self._workflow_id(self.workflow)
        d["status"] = self.status
        if self.time_period is not None:
            d["time_period"] = self.time_period
        return d

    @staticmethod
    def _workflow_id(value):
        if _is_uuid(value):
            return value
        client = get_cx_client()
        obj_id = get_cx_obj_id()
        workflows = client.get_agentic_workflows(obj_id)
        for workflow in workflows:
            if workflow["api_name"] == value:
                return workflow["id"]
        api_names = ", ".join(sorted(w["api_name"] for w in workflows)) or "(none)"
        raise ValueError(
            f"Unknown agentic workflow {value!r}. Reference workflows by uuid or "
            f"api_name. Available api_names: {api_names}"
        )


def _workflow_status_method(status, time_period=None):
    def method(self):
        return _AgenticWorkflowFilter(status, time_period, workflow=self.workflow)

    return method


class AgenticWorkflows:
    """
    "Agentic Workflows" filter category (formerly automations).

    Pass a workflow api_name or uuid to filter on a specific workflow, or the
    ellipsis literal for "Any Agentic Workflow":

        AgenticWorkflows("my_workflow_api_name").is_active()
        AgenticWorkflows(...).is_active()
    """

    def __init__(self, workflow):
        self.workflow = workflow

    is_active = _workflow_status_method("active")
    is_paused = _workflow_status_method("paused")
    is_inactive = _workflow_status_method("inactive")
    completed = _workflow_status_method("completed")
    cancelled = _workflow_status_method("cancelled")
    paused_on_failure = _workflow_status_method("paused_on_failure")
    was_never_active = _workflow_status_method("never")
    was_started = _workflow_status_method("was_started", "any_time")
    was_not_started = _workflow_status_method("was_not_started", "any_time")

    def status_any_of(self, *statuses):
        return _AgenticWorkflowFilter(list(statuses), workflow=self.workflow)


class _RelatedObjectFilter:
    def __init__(self, relationship_field, group, related_object=None):
        self.relationship_field = relationship_field
        self.group = group
        self.related_object = related_object

    def as_dict(self, parent=None):
        client = get_cx_client()
        obj_id = get_cx_obj_id()
        field = client.get_field(obj_id, self.relationship_field)
        if isinstance(self.group, dict):
            value = self.group
        elif self.related_object is not None:
            # the nested filters' field lookups run against the RELATED object
            with filter_context(self.related_object, client=client):
                value = self.group.as_dict()
        else:
            value = self.group.as_dict()
        return {
            "type": "related_object",
            "field_id": field["id"],
            "condition": "custom_filter",
            "subtype": "related_object_filter",
            "next_class_key": "fields",
            "value": value,
        }


class RelatedObject:
    """
    "Related Object" filter category: filter records by a nested filter on the
    records related through a relationship field.

    Pass related_object= (api_name or id of the related object) so the nested
    group's field lookups resolve against the related object:

        RelatedObject.has_filters(
            "primary_contact_record_0fe888",
            Any(Field("first_name") == "Bob"),
            related_object="client_client",
        )
    """

    @staticmethod
    def has_filters(relationship_field, group, related_object=None):
        return _RelatedObjectFilter(relationship_field, group, related_object)


class _FormFilter:
    def __init__(
        self, type_, subtype, form_id, submitted_condition, n_days=None, answer=None
    ):
        self.type = type_
        self.subtype = subtype
        self.form_id = form_id
        self.submitted_condition = submitted_condition
        self.n_days = n_days
        self.answer = answer

    def as_dict(self, parent=None):
        # The UI duplicates form_id/type/subtype at the top level and inside
        # payload; reproduce that shape exactly.
        payload = {
            "form_id": self.form_id,
            "type": self.type,
            "subtype": self.subtype,
            "submitted_condition": self.submitted_condition,
        }
        if self.n_days is not None:
            payload["n_days_value"] = str(self.n_days)
        if self.answer is None:
            payload["submission"] = "null"  # the literal string, as the UI sends it
        else:
            field_id, condition, value = self.answer
            payload["submission"] = "with_specific_answer"
            payload["field_id"] = field_id
            payload["answer"] = {
                "field_id": field_id,
                "condition": condition,
                "value": value,
            }
            payload["condition"] = condition
            payload["value"] = value
        return {
            "form_id": self.form_id,
            "type": self.type,
            "subtype": self.subtype,
            "payload": payload,
        }


class Forms:
    """
    "Forms" filter category. form_id is the form's uuid. answer= optionally
    narrows to a specific submitted answer as a (form_field_id, condition,
    value) tuple, e.g. ("<field-uuid>", "contains", "Alaska").
    """

    _TYPE = "forms_v2"
    _SUBTYPE = "form"

    @classmethod
    def submitted_any_time(cls, form_id, answer=None):
        return _FormFilter(
            cls._TYPE, cls._SUBTYPE, form_id, "submitted_any_time", answer=answer
        )

    @classmethod
    def submitted_within_days(cls, form_id, n_days, answer=None):
        return _FormFilter(
            cls._TYPE,
            cls._SUBTYPE,
            form_id,
            "submitted_past_n_days",
            n_days=n_days,
            answer=answer,
        )

    @classmethod
    def not_submitted_within_days(cls, form_id, n_days, answer=None):
        return _FormFilter(
            cls._TYPE,
            cls._SUBTYPE,
            form_id,
            "submitted_not_past_n_days",
            n_days=n_days,
            answer=answer,
        )

    @classmethod
    def never_submitted(cls, form_id, answer=None):
        return _FormFilter(
            cls._TYPE, cls._SUBTYPE, form_id, "never_submitted", answer=answer
        )


class Surveys(Forms):
    """ "Surveys" filter category — identical shape to Forms, different type tokens."""

    _TYPE = "surveys"
    _SUBTYPE = "survey"


class Options:
    def __init__(self, *values):
        self.values = values

    def __contains__(self, field):
        if isinstance(field, Field):
            condition = field.is_any_of(*self.values)
            # Store in thread-local storage using a stack to handle multiple operations
            if not hasattr(_local_filter_cx, "in_result_stack"):
                _local_filter_cx.in_result_stack = []
            _local_filter_cx.in_result_stack.append(condition)
            return True
        return False


# ---------------------------------------------------------------------------
# Output envelopes
#
# The same group grammar is wrapped differently by each consumer:
#   records search body:      {"query": [groups], "and": bool}
#   condition filter_config:  {"and": bool, "query": [groups+ids], "invalid": False}
#   dashlet custom_filters:   {"custom_filters": {"and": bool, "query": [groups+ids]}}
# ---------------------------------------------------------------------------


def _as_root_dict(expr):
    """Serialize an All/Any/condition to the root {"and": ..., "query": [...]} shape."""
    if isinstance(expr, dict):
        return expr
    d = expr.as_dict()
    if "query" not in d:  # bare condition -> wrap in a single AND group
        d = {"and": True, "query": [{"and": True, "filters": [d]}]}
    return d


def _with_group_ids(groups):
    """Group dicts with the sequential ids the UI (and condition steps) require."""
    return [{"id": f"query-{i}", **g} for i, g in enumerate(groups)]


def as_search_body(expr):
    """Records-search POST body: {"query": [...], "and": bool}."""
    d = _as_root_dict(expr)
    return {"query": d["query"], "and": d["and"]}


def as_filter_config(expr):
    """Automation condition-step ``filter_config`` (group ids required;
    ``value`` must never be null — guaranteed by the DSL's condition builders)."""
    d = _as_root_dict(expr)
    return {"and": d["and"], "query": _with_group_ids(d["query"]), "invalid": False}


def as_custom_filters(expr):
    """Dashlet ``custom_filters`` wrapper."""
    d = _as_root_dict(expr)
    return {"custom_filters": {"and": d["and"], "query": _with_group_ids(d["query"])}}


# ---------------------------------------------------------------------------
# JSON filter specs
#
# The DSL is Python-facing; CLI callers (and automation spec files) express
# filters as JSON instead. A spec is either a group or a condition:
#
#   {"all": [<spec>, ...]}   AND group          {"any": [...]}   OR group
#   {"field": "<api_name or uuid>", "op": "<op>", "value": <v>}
#
# Field names, option labels, and tag names resolve to UUIDs exactly as in
# the DSL (the spec compiles TO the DSL). Ops with no value (is_blank,
# is_me, ...) omit "value"; list ops (is_any_of, between, ...) take a list.
# ---------------------------------------------------------------------------

_NO_VALUE_OPS = {
    "is_blank": lambda f: f.is_blank(),
    "not_blank": lambda f: f.not_blank(),
    "is_me": lambda f: f.is_me(),
    "is_checked": lambda f: f.is_checked(),
    "not_checked": lambda f: f.not_checked(),
}

_SCALAR_OPS = {
    "=": lambda f, v: f.equals(v),
    "!=": lambda f, v: f.not_equals(v),
    "<": lambda f, v: f < v,
    "<=": lambda f, v: f <= v,
    ">": lambda f, v: f > v,
    ">=": lambda f, v: f >= v,
    "contains": lambda f, v: f.contains(v),
    "not_contains": lambda f, v: f.not_contains(v),
    "starts_with": lambda f, v: f.startswith(v),
    "ends_with": lambda f, v: f.endswith(v),
    "not_starts_with": lambda f, v: f.not_startswith(v),
    "not_ends_with": lambda f, v: f.not_endswith(v),
    "month_equals": lambda f, v: f.month_equals(v),
}

_LIST_OPS = {
    "is_any_of": lambda f, v: f.is_any_of(*v),
    "not_any_of": lambda f, v: f.not_any_of(*v),
    "has_any": lambda f, v: f.has_any(*v),
    "has_all": lambda f, v: f.has_all(*v),
    "has_none": lambda f, v: f.has_none(*v),
    "between": lambda f, v: f.between(*v),
}

VALID_SPEC_OPS = sorted({*_NO_VALUE_OPS, *_SCALAR_OPS, *_LIST_OPS})


def _spec_op_condition_map():
    """Map each JSON spec op name to the internal DSL condition token it
    produces. Building a FilterCondition doesn't touch the schema/client (that
    only happens in .as_dict()), so this needs no live env."""
    f = Field("_")
    mapping = {}
    for name, fn in _NO_VALUE_OPS.items():
        mapping[name] = fn(f).condition
    for name, fn in _SCALAR_OPS.items():
        mapping[name] = fn(f, "x").condition
    for name, fn in _LIST_OPS.items():
        mapping[name] = fn(f, ["x", "y"]).condition
    return mapping


def field_type_ops(field_type=None):
    """Valid ``--filter`` spec ops per field type.

    Derived from ``_UI_SUPPORTED_CONDITIONS`` (the Kizen UI's own condition
    list) translated to the spec-level op names a JSON ``--filter`` actually
    uses — a single source of truth, so this can't drift from what
    :func:`render_search_filters` accepts. Pass a field_type to get just its
    ops; omit it for every known field type.

    Not covered: the "stage" field's own condition set (time_in_stage,
    entered_stage, left_stage — DSL-only, no spec op exposes them) and the
    narrower override some default fields get (created, updated, owner,
    email_status) — see ``_STAGE_CONDITIONS`` / ``_DEFAULT_FIELD_CONDITION_OVERRIDES``.
    """
    # is_checked/not_checked are checkbox-only spelling of `= True`/`= False`;
    # excluded from the general reverse map so they don't bleed into every
    # other "=" (text, dropdown, ...) field type.
    _CHECKBOX_ONLY_OPS = {"is_checked", "not_checked"}
    condition_to_ops = {}
    for op, condition in _spec_op_condition_map().items():
        if op in _CHECKBOX_ONLY_OPS:
            continue
        condition_to_ops.setdefault(condition, []).append(op)

    def ops_for(conditions, extra=()):
        ops = set(extra)
        for condition in conditions:
            ops.update(condition_to_ops.get(condition, ()))
        return sorted(ops)

    def extras_for(ft):
        return _CHECKBOX_ONLY_OPS if ft == "checkbox" else ()

    if field_type is not None:
        if field_type not in _UI_SUPPORTED_CONDITIONS:
            raise ValueError(
                f"unknown field_type {field_type!r}. Known: "
                f"{', '.join(sorted(_UI_SUPPORTED_CONDITIONS))}"
            )
        return ops_for(_UI_SUPPORTED_CONDITIONS[field_type], extras_for(field_type))

    return {
        ft: ops_for(conditions, extras_for(ft))
        for ft, conditions in sorted(_UI_SUPPORTED_CONDITIONS.items())
    }


def from_spec(spec):
    """Compile a JSON filter spec (see module comment above) to a DSL
    expression. Raises ValueError on malformed specs; UUID resolution and
    UI-supportability checks happen later, at serialization time."""
    if not isinstance(spec, dict):
        raise ValueError(
            f"filter spec must be a JSON object, got {type(spec).__name__}"
        )

    if "all" in spec or "any" in spec:
        if len(spec) != 1:
            raise ValueError('a group must be exactly {"all": [...]} or {"any": [...]}')
        ((kind, children),) = spec.items()
        if not isinstance(children, list) or not children:
            raise ValueError(f'"{kind}" must be a non-empty list of specs')
        group_cls = All if kind == "all" else Any
        return group_cls(*[from_spec(c) for c in children])

    if "field" in spec:
        unknown = set(spec) - {"field", "op", "value"}
        if unknown:
            raise ValueError(f"unknown keys in condition: {sorted(unknown)}")
        op = spec.get("op")
        field = Field(spec["field"])
        if op in _NO_VALUE_OPS:
            if "value" in spec:
                raise ValueError(f'op "{op}" takes no "value"')
            return _NO_VALUE_OPS[op](field)
        if op in _SCALAR_OPS:
            if "value" not in spec:
                raise ValueError(f'op "{op}" requires a "value"')
            return _SCALAR_OPS[op](field, spec["value"])
        if op in _LIST_OPS:
            if not isinstance(spec.get("value"), list):
                raise ValueError(f'op "{op}" requires a list "value"')
            return _LIST_OPS[op](field, spec["value"])
        raise ValueError(f"unknown op {op!r}. Valid ops: {', '.join(VALID_SPEC_OPS)}")

    raise ValueError(
        f'filter spec must contain "all", "any", or "field" (got keys: {sorted(spec)})'
    )


def render_search_filters(spec, object_api_name, client=None):
    """CLI entry point: JSON filter -> list of filter groups for records search.

    A spec with a top-level "query" key is treated as already-rendered Kizen
    filter groups and passed through (after normalization); otherwise it is
    compiled via :func:`from_spec` with lookups against ``object_api_name``.
    """
    if "query" in spec:
        return normalize_filter_config(spec)["query"]
    with filter_context(object_api_name, client=client):
        return as_search_body(from_spec(spec))["query"]


def normalize_filter_config(cfg):
    """Validate/normalize a raw filter-config dict (condition steps, dashlets).

    Ensures the {"and", "query": [{"id", "and", "filters": [...]}]} structure,
    assigns missing sequential group ids, and rejects null clause values (the
    API 400s on them). Existing ids and unknown clause keys pass through.
    """
    if not isinstance(cfg, dict) or not isinstance(cfg.get("query"), list):
        raise ValueError('filter config must be a dict with a "query" list')
    groups = []
    for i, group in enumerate(cfg["query"]):
        if not isinstance(group, dict) or not isinstance(group.get("filters"), list):
            raise ValueError(f'query[{i}] must be a dict with a "filters" list')
        for j, clause in enumerate(group["filters"]):
            if not isinstance(clause, dict):
                raise ValueError(f"query[{i}].filters[{j}] must be a dict")
            if "value" in clause and clause["value"] is None:
                raise ValueError(
                    f'query[{i}].filters[{j}] has null "value" — the API rejects '
                    'null; use false for blank checks or "" for empty text'
                )
        groups.append(
            {
                "id": group.get("id") or f"query-{i}",
                **{k: v for k, v in group.items() if k != "id"},
            }
        )
    return {
        "and": cfg.get("and", True),
        "query": groups,
        "invalid": cfg.get("invalid", False),
    }
