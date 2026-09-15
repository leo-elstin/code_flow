"""Validates _PLAN_JSON_SCHEMA itself — the highest-consequence part of the
SDK planner migration, since a broken schema blocks planning entirely (there
is no fallback artifact the way a truncated dev run still has real diffs).
Requires the real `jsonschema` package (already a transitive dependency via
mcp/fastapi), not claude-agent-sdk — this checks the schema's own structure
and realistic payloads against it, independent of the SDK being installed."""
import pytest

jsonschema = pytest.importorskip("jsonschema")

from app.agents.roles.planner_sdk import _PLAN_JSON_SCHEMA


def test_schema_is_valid_json_schema():
    jsonschema.Draft7Validator.check_schema(_PLAN_JSON_SCHEMA)


def test_every_object_forbids_additional_properties():
    """additionalProperties:false is the one documented hard constraint for
    structured-output schemas — missing it anywhere is the kind of mistake
    that only surfaces as a live API rejection, so it's worth asserting
    statically instead."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(_PLAN_JSON_SCHEMA)


def test_questions_only_shape_validates():
    payload = {
        "feature_summary": "",
        "reasoning": "",
        "questions": [
            {
                "id": "q0",
                "question": "Use the in-house remote-config service or a new SDK?",
                "context": "both patterns exist in the codebase",
                "options": [
                    {"id": "A", "label": "In-house service", "description": "existing pattern"},
                    {"id": "B", "label": "New SDK", "description": "adds a dependency"},
                ],
            }
        ],
        "files_to_create": [],
        "files_to_modify": [],
        "context_files": [],
        "acceptance_criteria": [],
        "discovery_evidence": [],
        "plan_markdown": "Awaiting clarification",
    }
    jsonschema.validate(payload, _PLAN_JSON_SCHEMA)


def test_full_plan_shape_validates():
    payload = {
        "feature_summary": "Add dark mode toggle",
        "reasoning": "existing theme provider found",
        "questions": [],
        "files_to_create": [
            {"path": "lib/features/theme/theme_toggle.dart", "purpose": "new widget"}
        ],
        "files_to_modify": [
            {"path": "lib/core/di/dependency_injector.dart", "purpose": "register provider"}
        ],
        "context_files": ["lib/core/theme/app_theme.dart"],
        "architecture": {
            "layers": "presentation/domain",
            "state_management": "bloc",
            "routing": "go_router",
        },
        "acceptance_criteria": ["Toggle persists across restarts"],
        "discovery_evidence": ["found ThemeCubit in lib/core/theme/theme_cubit.dart"],
        "plan_markdown": "# Dark mode toggle\n\n## Summary\n...",
    }
    jsonschema.validate(payload, _PLAN_JSON_SCHEMA)


def test_plan_missing_a_required_field_is_rejected():
    payload = {
        "feature_summary": "x",
        "questions": [],
        "files_to_create": [],
        "files_to_modify": [],
        "context_files": [],
        "acceptance_criteria": [],
        # plan_markdown deliberately omitted
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, _PLAN_JSON_SCHEMA)


def test_unexpected_top_level_field_is_rejected():
    payload = {
        "feature_summary": "x",
        "questions": [],
        "files_to_create": [],
        "files_to_modify": [],
        "context_files": [],
        "acceptance_criteria": [],
        "discovery_evidence": [],
        "plan_markdown": "x",
        "unexpected_field": "should not be allowed",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, _PLAN_JSON_SCHEMA)
