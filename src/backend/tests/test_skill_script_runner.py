from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent_skills.backends import CompositeBackend, FilesystemBackend
from agent_skills.loader import MultiSourceSkillLoader
from agent_skills.script_runner import (
    _find_script_declaration,
    _params_to_cli_args,
    _resolve_timeout,
)


def _builtin_only_loader() -> MultiSourceSkillLoader:
    skills_root = Path(__file__).resolve().parents[1] / "agent_skills" / "skills"
    backend = CompositeBackend([
        FilesystemBackend(skills_root, "built-in", priority=0),
    ])
    return MultiSourceSkillLoader(backend=backend)


def test_find_script_declaration_accepts_unique_basename():
    spec = SimpleNamespace(executable_scripts=[
        {"name": "scripts/make.sh"},
        {"name": "scripts/cover.py"},
    ])

    decl = _find_script_declaration(spec, "make.sh")

    assert decl is not None
    assert decl["name"] == "scripts/make.sh"


def test_params_to_cli_args_keeps_command_positional():
    args = _params_to_cli_args(
        {
            "command": "run",
            "title": "随机PDF",
            "type": "report",
        },
        {"params_schema": None},
    )

    assert args == {
        "_args": ["run", "--title", "随机PDF", "--type", "report"],
    }


def test_params_to_cli_args_passthrough_args_list():
    args = _params_to_cli_args(
        {"_args": ["demo"]},
        {"params_schema": None},
    )

    assert args == {
        "_args": ["demo"],
    }


def test_params_to_cli_args_serializes_dict_values_as_json():
    """dict/list values must become valid JSON, not Python repr, so downstream
    CLIs (e.g. --content-json '[...]') can parse them."""
    args = _params_to_cli_args(
        {
            "command": "create",
            "content-json": {"sections": [{"heading": "引言", "level": 1}]},
        },
        {"params_schema": None},
    )

    positional_and_flags = args["_args"]
    assert positional_and_flags[0] == "create"
    # Find the --content-json flag value
    idx = positional_and_flags.index("--content-json")
    value = positional_and_flags[idx + 1]
    # Must be valid JSON, not Python repr (no single quotes around keys)
    assert "'" not in value
    assert json.loads(value) == {"sections": [{"heading": "引言", "level": 1}]}


def test_params_to_cli_args_serializes_bool_lowercase():
    args = _params_to_cli_args(
        {"command": "create", "verbose": True, "silent": False},
        {"params_schema": None},
    )

    # True → bare flag; False → "--silent false"
    assert "--verbose" in args["_args"]
    idx = args["_args"].index("--silent")
    assert args["_args"][idx + 1] == "false"


def test_params_to_cli_args_passthrough_forwards_non_args_keys():
    """When _args is supplied the wrapper must still forward other keys so they
    reach the sidecar's stdin JSON."""
    args = _params_to_cli_args(
        {
            "_args": ["create", "--output", "out.docx", "--title", "报告"],
            "content": {
                "sections": [
                    {"heading": "引言", "level": 1, "paragraphs": ["第一段正文"]},
                ]
            },
        },
        {"params_schema": None},
    )

    assert args["_args"] == [
        "create",
        "--output",
        "out.docx",
        "--title",
        "报告",
    ]
    assert args["content"] == {
        "sections": [
            {"heading": "引言", "level": 1, "paragraphs": ["第一段正文"]},
        ]
    }


def test_resolve_timeout_prefers_script_declaration():
    timeout = _resolve_timeout(
        {"timeout": 30, "max_timeout": 120},
        {"timeout": 120},
        None,
    )

    assert timeout == 120


def test_resolve_timeout_caps_requested_timeout():
    timeout = _resolve_timeout(
        {"timeout": 30, "max_timeout": 120},
        {"timeout": 60},
        300,
    )

    assert timeout == 120
