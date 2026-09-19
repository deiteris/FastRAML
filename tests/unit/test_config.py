"""Common FastRAML configuration shared by parsing commands."""

from __future__ import annotations

from fastraml.cli import EXIT_INVALID, EXIT_OK, main
from fastraml.config import parse_config

OLD = """#%RAML 1.0
title: T
/things:
  get:
    protocols: [HTTP, HTTPS]
"""

NEW = OLD.replace('[HTTP, HTTPS]', '[HTTPS]')


def test_parser_and_view_sections_decode_together(tmp_path):
    config = parse_config(
        """version: 1
parser:
  workspaceRoot: workspace
  maxIncludeSize: 12345
  maxDepth: 321
  regexEngine: re2
  remote: true
lint:
  extends: [recommended]
compatibility:
  rules:
    - id: protocol-removed
      impact: compatible
""",
        base_dir=tmp_path,
    )
    assert config.parser.workspace_root == str(tmp_path / 'workspace')
    assert config.parser.max_include_size == 12345
    assert config.parser.max_depth == 321
    assert config.parser.regex_engine == 're2'
    assert config.parser.remote is True
    assert config.lint == {'extends': ['recommended']}
    assert config.compatibility.rules[0].id == 'protocol-removed'


def test_precise_protocol_override_changes_the_exit_code(workspace, tmp_path, capsys):
    root = workspace({'old.raml': OLD, 'new.raml': NEW})
    config = tmp_path / 'fastraml.yaml'
    config.write_text(
        """compatibility:
  rules:
    - id: protocol-removed
      impact: compatible
      match:
        attribute: protocols
        before: [HTTP, HTTPS]
        after: [HTTPS]
""",
        encoding='utf-8',
    )
    args = ['diff', '--config', str(config), str(root / 'old.raml'), str(root / 'new.raml')]
    assert main(args) == EXIT_OK
    output = capsys.readouterr().out
    assert '| `protocols` | `HTTP`, `HTTPS` -> `HTTPS` | Compatible |' in output


def test_nonmatching_protocol_override_does_not_hide_a_break(workspace, tmp_path, capsys):
    root = workspace({'old.raml': OLD, 'new.raml': NEW})
    config = tmp_path / 'fastraml.yaml'
    config.write_text(
        """compatibility:
  rules:
    - id: protocol-removed
      impact: compatible
      match:
        before: [HTTP, HTTPS]
        after: [HTTP]
""",
        encoding='utf-8',
    )
    assert main(['diff', '--config', str(config), str(root / 'old.raml'), str(root / 'new.raml')]) == EXIT_INVALID
    assert '| `protocols` | `HTTP`, `HTTPS` -> `HTTPS` | Breaking |' in capsys.readouterr().out


def test_disabled_compatibility_rule_is_absent_and_nonblocking(workspace, tmp_path, capsys):
    root = workspace({'old.raml': OLD, 'new.raml': NEW})
    config = tmp_path / 'fastraml.yaml'
    config.write_text(
        """compatibility:
  rules:
    - id: protocol-removed
      disabled: true
""",
        encoding='utf-8',
    )
    assert main(['diff', '--config', str(config), str(root / 'old.raml'), str(root / 'new.raml')]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ''


def test_cli_rule_override_applies_after_the_file(workspace, tmp_path, capsys):
    root = workspace({'old.raml': OLD, 'new.raml': NEW})
    config = tmp_path / 'fastraml.yaml'
    config.write_text(
        """compatibility:
  rules:
    - id: protocol-removed
      impact: breaking
""",
        encoding='utf-8',
    )
    args = [
        'diff',
        '--config',
        str(config),
        '--rule',
        'protocol-removed=compatible',
        str(root / 'old.raml'),
        str(root / 'new.raml'),
    ]
    assert main(args) == EXIT_OK
    assert '| `protocols` | `HTTP`, `HTTPS` -> `HTTPS` | Compatible |' in capsys.readouterr().out


def test_parser_workspace_root_applies_to_validate(tmp_path, capsys):
    workspace_root = tmp_path / 'workspace'
    api = workspace_root / 'api'
    shared = workspace_root / 'shared'
    api.mkdir(parents=True)
    shared.mkdir()
    (api / 'api.raml').write_text(
        '#%RAML 1.0\ntitle: T\ntypes:\n  Shared: !include ../shared/shared.raml\n',
        encoding='utf-8',
    )
    (shared / 'shared.raml').write_text('#%RAML 1.0 DataType\ntype: string\n', encoding='utf-8')
    config = tmp_path / 'fastraml.yaml'
    config.write_text('parser:\n  workspaceRoot: workspace\n', encoding='utf-8')

    assert main(['validate', '--config', str(config), str(api / 'api.raml')]) == EXIT_OK
    assert capsys.readouterr().err == ''


def test_unknown_compatibility_rule_is_rejected(workspace, tmp_path, capsys):
    root = workspace({'old.raml': OLD, 'new.raml': NEW})
    config = tmp_path / 'fastraml.yaml'
    config.write_text(
        'compatibility:\n  rules:\n    - id: imaginary-rule\n      disabled: true\n',
        encoding='utf-8',
    )
    assert main(['diff', '--config', str(config), str(root / 'old.raml'), str(root / 'new.raml')]) == EXIT_INVALID
    assert 'unknown compatibility rule: imaginary-rule' in capsys.readouterr().err
