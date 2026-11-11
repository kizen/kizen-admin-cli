"""Tests for the dashlet-config generator backing `kizen dashboards dashlet-config`.

Covers both input modes without a live env:
  * template mode — no refs → <...> placeholder tokens, no API calls;
  * resolved mode — api_name refs → real UUIDs baked in (get_object mocked).
The generated configs are asserted to match the tools.dashboards builders they
wrap, since those remain the wire-format source of truth (SOL-115).
"""

from __future__ import annotations

import pytest

from kizen_builder.tools import dashlet_templates as tpl

OBJECT_ID = "18a93b82-9925-4fd8-9c18-7d291412f0fa"
FIELD_ID = "ce136267-283e-4c3f-9d24-7184c66de6d8"
ACTIVITY_ID = "0c01c7a5-bc95-4d49-8d93-8a33e9ab3950"

FAKE_OBJECT = {
    "id": OBJECT_ID,
    "api_name": "clinics",
    "fields": [
        {
            "api_name": "status",
            "id": FIELD_ID,
            "display_name": "Status",
            "deleted": False,
        },
        {"api_name": "old", "id": "dead", "display_name": "Old", "deleted": True},
    ],
}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_every_catalog_type_builds_in_template_mode():
    """Each declared type must have a working builder (no drift TYPES↔dispatch)."""
    for dt in tpl.available_types():
        cfg = tpl.build_dashlet_config(dt.key)
        assert cfg["report_type"] == dt.report_type
        assert cfg["chart_type"] == dt.chart_type


def test_unknown_type_lists_valid_types():
    with pytest.raises(ValueError, match="unknown dashlet type"):
        tpl.build_dashlet_config("not_a_type")


# ---------------------------------------------------------------------------
# Template mode — placeholders, no live calls
# ---------------------------------------------------------------------------


def test_template_mode_uses_placeholder_object_and_field():
    cfg = tpl.build_dashlet_config("field_breakdown")
    assert cfg["object_id"] == tpl.OBJECT_PLACEHOLDER
    assert cfg["field"] == tpl.FIELD_PLACEHOLDER
    assert tpl.has_placeholders(cfg)


def test_table_of_records_template_has_one_placeholder_column():
    cfg = tpl.build_dashlet_config("table_of_records")
    cols = cfg["fe_extra_info"]["columns"]
    assert len(cols) == 1
    assert cols[0]["id"] == tpl.FIELD_PLACEHOLDER


def test_field_range_breakdown_template_has_example_buckets():
    cfg = tpl.build_dashlet_config("field_range_breakdown")
    buckets = cfg["metric_type_extra_info"]["fields_range_breakdown"]["buckets"]
    assert len(buckets) == 2
    assert buckets[0]["min"] == {"value": 0, "operator": ">="}


def test_email_metric_template_needs_no_object():
    cfg = tpl.build_dashlet_config("email_metric")
    assert cfg["entity_type"] == "email"
    assert "object_id" not in cfg
    assert not tpl.has_placeholders(cfg)


def test_html_template_is_placeholder_free():
    cfg = tpl.build_dashlet_config("html")
    assert cfg["report_type"] == "html"
    assert not tpl.has_placeholders(cfg)


# ---------------------------------------------------------------------------
# Parameterized metric families honor overrides + line-needs-frequency default
# ---------------------------------------------------------------------------


def test_pipeline_metric_line_defaults_frequency_month():
    cfg = tpl.build_dashlet_config("pipeline_metric", chart_type="line")
    assert cfg["frequency"] == "month"


def test_pipeline_metric_numeric_omits_frequency():
    cfg = tpl.build_dashlet_config("pipeline_metric", chart_type="numeric")
    assert "frequency" not in cfg


def test_report_type_override_flows_through():
    cfg = tpl.build_dashlet_config(
        "pipeline_metric", report_type="records_won", chart_type="numeric"
    )
    assert cfg["report_type"] == "records_won"


def test_explicit_frequency_beats_default():
    cfg = tpl.build_dashlet_config(
        "activity_metric", chart_type="line", frequency="day"
    )
    assert cfg["frequency"] == "day"


# ---------------------------------------------------------------------------
# Resolved mode — api_names → real UUIDs (get_object / activity resolver mocked)
# ---------------------------------------------------------------------------


def test_resolved_mode_bakes_object_and_field_uuids(monkeypatch):
    monkeypatch.setattr(tpl, "get_object", lambda ref: FAKE_OBJECT)
    cfg = tpl.build_dashlet_config(
        "field_breakdown", object_ref="clinics", field_ref="status"
    )
    assert cfg["object_id"] == OBJECT_ID
    assert cfg["field"] == FIELD_ID
    assert not tpl.has_placeholders(cfg)


def test_table_of_records_resolved_column_uses_display_name(monkeypatch):
    monkeypatch.setattr(tpl, "get_object", lambda ref: FAKE_OBJECT)
    cfg = tpl.build_dashlet_config(
        "table_of_records", object_ref="clinics", field_ref="status"
    )
    col = cfg["fe_extra_info"]["columns"][0]
    assert col["id"] == FIELD_ID
    assert col["label"] == "Status"


def test_field_ref_without_object_errors():
    with pytest.raises(ValueError, match="pass --object"):
        tpl.build_dashlet_config("field_breakdown", field_ref="status")


def test_missing_field_raises_lookup(monkeypatch):
    monkeypatch.setattr(tpl, "get_object", lambda ref: FAKE_OBJECT)
    with pytest.raises(LookupError, match="not found"):
        tpl.build_dashlet_config(
            "field_breakdown", object_ref="clinics", field_ref="nope"
        )


def test_deleted_field_is_not_matched(monkeypatch):
    monkeypatch.setattr(tpl, "get_object", lambda ref: FAKE_OBJECT)
    with pytest.raises(LookupError):
        tpl.build_dashlet_config(
            "field_breakdown", object_ref="clinics", field_ref="old"
        )


def test_activity_object_resolves_via_activity_resolver(monkeypatch):
    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tpl, "load_env_config", lambda: object())
    monkeypatch.setattr(tpl, "KizenClient", lambda config: _Ctx())
    monkeypatch.setattr(
        tpl, "resolve_activity_id", lambda client, ref: (ACTIVITY_ID, "Call")
    )
    cfg = tpl.build_dashlet_config("scheduled_activities", object_ref="call")
    assert cfg["object_id"] == ACTIVITY_ID


# ---------------------------------------------------------------------------
# wrap_as_dashboard
# ---------------------------------------------------------------------------


def test_wrap_custom_object_type_is_homepage():
    cfg = tpl.build_dashlet_config("field_sum")
    dash = tpl.wrap_as_dashboard("field_sum", cfg)
    assert dash["type"] == "homepage"
    assert dash["dashlets"][0]["config"] is cfg
    assert dash["dashlets"][0]["custom_object"] == tpl.OBJECT_PLACEHOLDER


def test_wrap_pipeline_type_is_generic_dashboard():
    cfg = tpl.build_dashlet_config("pipeline_metric")
    dash = tpl.wrap_as_dashboard("pipeline_metric", cfg)
    assert dash["type"] == "generic_dashboard"


def test_wrap_email_type_has_null_custom_object():
    cfg = tpl.build_dashlet_config("email_metric")
    dash = tpl.wrap_as_dashboard("email_metric", cfg)
    assert dash["dashlets"][0]["custom_object"] is None


def test_wrap_invents_no_layout_and_no_published_flag():
    """`--full` has no geometry to offer and no reason to publish, so the
    wrapper leaves both for the author (or the server) to decide."""
    cfg = tpl.build_dashlet_config("pipeline_metric")
    dash = tpl.wrap_as_dashboard("pipeline_metric", cfg)
    assert "published" not in dash
    assert "layout" not in dash["dashlets"][0]


@pytest.mark.parametrize("dashlet_type", list(tpl.TYPES))
def test_no_template_carries_dashlet_style_config(dashlet_type):
    cfg = tpl.build_dashlet_config(dashlet_type)
    assert "dashlet_style_config" not in (cfg.get("fe_extra_info") or {})
    assert "dashlet_style_config" not in str(cfg)


def test_generate_carries_custom_object_for_table_without_config_object_id(monkeypatch):
    # table_of_records config has no object_id — the object lives only on the
    # dashlet envelope, so generate() must surface it as custom_object.
    monkeypatch.setattr(tpl, "get_object", lambda ref: FAKE_OBJECT)
    gen = tpl.generate("table_of_records", object_ref="clinics", field_ref="status")
    assert "object_id" not in gen.config
    assert gen.custom_object == OBJECT_ID
    dash = tpl.wrap_as_dashboard(
        "table_of_records", gen.config, custom_object=gen.custom_object
    )
    assert dash["dashlets"][0]["custom_object"] == OBJECT_ID
    assert dash["type"] == "homepage"


def test_generate_null_custom_object_for_activity_family(monkeypatch):
    # activity dashlets carry object_id in-config, not as the dashlet envelope's
    # custom_object.
    monkeypatch.setattr(tpl, "load_env_config", lambda: object())

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tpl, "KizenClient", lambda config: _Ctx())
    monkeypatch.setattr(tpl, "resolve_activity_id", lambda c, r: (ACTIVITY_ID, "Call"))
    gen = tpl.generate("scheduled_activities", object_ref="call")
    assert gen.custom_object is None
    assert gen.config["object_id"] == ACTIVITY_ID


# ---------------------------------------------------------------------------
# --filter: a filter spec compiled into config.filters
# ---------------------------------------------------------------------------

MINE = {"all": [{"field": "owner", "op": "is_me"}]}


@pytest.fixture
def policies(monkeypatch, kizen):
    """'policies' resolves to the filter stub's object, so specs compile offline."""
    from tests.filter_stub import POLICIES_OBJ_ID

    obj = {"id": POLICIES_OBJ_ID, "api_name": "policies", "fields": []}
    monkeypatch.setattr(tpl, "get_object", lambda ref: obj)
    return POLICIES_OBJ_ID


@pytest.mark.parametrize("dashlet_type", tpl.FILTERABLE_TYPES)
def test_filter_spec_compiles_owner_and_team_member_is_me(policies, dashlet_type):
    spec = {
        "all": [
            {"field": "owner", "op": "is_me"},
            {"field": "fteammember", "op": "is_me"},
        ]
    }
    cfg = tpl.build_dashlet_config(
        dashlet_type, object_ref="policies", filter_spec=spec
    )
    assert cfg["filters"] == {
        "custom_filters": {
            "and": True,
            "query": [
                {
                    "id": "query-0",
                    "and": True,
                    "filters": [
                        {
                            "type": "fields",
                            "subtype": "non_custom",
                            "field": "owner",
                            "condition": "=",
                            "value": "is_me",
                        },
                        {
                            "type": "fields_v2",
                            "subtype": "custom",
                            "field": '"custom"::0a40f245-a9bd-47df-bf1c-8a88583d29c5',
                            "condition": "=",
                            "value": "is_me",
                        },
                    ],
                }
            ],
        }
    }


def test_filter_spec_leaves_the_rest_of_the_config_alone(policies):
    plain = tpl.build_dashlet_config("field_sum", object_ref="policies")
    filtered = tpl.build_dashlet_config(
        "field_sum", object_ref="policies", filter_spec=MINE
    )
    assert plain["filters"] == {"custom_filters": {"and": True, "query": []}}
    assert {k: v for k, v in filtered.items() if k != "filters"} == {
        k: v for k, v in plain.items() if k != "filters"
    }


def test_filter_spec_needs_an_object():
    with pytest.raises(ValueError, match="needs --object"):
        tpl.build_dashlet_config("field_sum", filter_spec=MINE)


def test_filter_spec_on_a_type_without_record_filters_errors():
    with pytest.raises(ValueError, match="has no record filter"):
        tpl.build_dashlet_config(
            "pipeline_metric", object_ref="deals", filter_spec=MINE
        )


def test_bad_filter_field_is_the_dsl_message_prefixed(policies):
    with pytest.raises(ValueError, match="^invalid filter spec: Unknown field 'nope'"):
        tpl.build_dashlet_config(
            "field_sum",
            object_ref="policies",
            filter_spec={"all": [{"field": "nope", "op": "is_me"}]},
        )
