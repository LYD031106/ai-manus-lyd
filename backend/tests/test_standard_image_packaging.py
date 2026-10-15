"""Packaging contracts shared by classic Docker, Compose and GitHub builds.

These checks need no Docker daemon or model call. They verify that the declared
contexts can provide every COPY source and both sandbox variants ship the same
recipe used by the backend prompt.
"""
from pathlib import Path
import shlex

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def copy_instructions(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            tokens = shlex.split(line)
            yield tokens[1:-1], tokens[-1]


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose-development.yml"])
def test_standard_compose_builds_have_all_copy_sources_in_context(filename):
    services = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))["services"]
    for component in ("backend", "sandbox"):
        build = services[component]["build"]
        assert build["context"] == "."
        assert build["dockerfile"] == f"{component}/Dockerfile"
        for sources, _ in copy_instructions(ROOT / build["dockerfile"]):
            for source in sources:
                assert source != ".", "A blanket root COPY would include unrelated components"
                assert (ROOT / source).exists(), f"Missing COPY source: {source}"


def test_both_image_variants_include_backend_prompt_and_complete_sandbox_recipe():
    for dockerfile in ("Dockerfile", "Dockerfile.lightweight"):
        backend_copies = list(copy_instructions(ROOT / "backend" / dockerfile))
        assert any(
            "builtin_skills/s127-gml/SKILL.md" in sources
            and destination.endswith("builtin_skills/s127-gml/SKILL.md")
            for sources, destination in backend_copies
        )
        sandbox_copies = list(copy_instructions(ROOT / "sandbox" / dockerfile))
        assert (["builtin_skills/s127-gml"], "/opt/skills/s127-gml") in sandbox_copies
    for relative in ("SKILL.md", "scripts/parse_office.py", "scripts/build_gml.py", "schemas/S127.xsd"):
        assert (ROOT / "builtin_skills/s127-gml" / relative).is_file()


def test_development_source_mount_cannot_hide_backend_recipe():
    services = yaml.safe_load((ROOT / "docker-compose-development.yml").read_text(encoding="utf-8"))["services"]
    assert "./backend:/app" in services["backend"]["volumes"]
    assert "./builtin_skills/s127-gml:/app/builtin_skills/s127-gml:ro" in services["backend"]["volumes"]
    assert "./builtin_skills/s127-gml:/opt/skills/s127-gml:ro" in services["sandbox"]["volumes"]


def test_ci_uses_the_same_context_contract_and_keeps_other_components_separate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-build-and-push.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["build-and-push"]
    contexts = {entry["component"]: entry["context"] for entry in job["strategy"]["matrix"]["include"]}
    assert contexts == {"frontend": "./frontend", "backend": ".", "sandbox": ".", "claw": "./claw"}
    build = next(step["with"] for step in job["steps"] if step.get("uses", "").startswith("docker/build-push-action@"))
    assert build["context"] == "${{ matrix.context }}"
    assert build["file"] == "./${{ matrix.component }}/Dockerfile"


def test_root_context_allowlist_contains_required_sources_and_excludes_secrets_and_cache():
    rules = [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    assert rules[0] == "**"
    for component in ("backend", "sandbox"):
        for source in ("Dockerfile", "pyproject.toml", "README.md"):
            assert f"!{component}/{source}" in rules
    assert "!backend/scripts/*.py" in rules
    assert "!backend/start_worker.sh" in rules
    assert "!sandbox/supervisord.conf" in rules
    assert "!builtin_skills/s127-gml/**" in rules
    final_allow = max(i for i, rule in enumerate(rules) if rule.startswith("!"))
    for rule in ("**/.env", "**/.env.*", "**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**", "**/.venv/**"):
        assert rules.index(rule) > final_allow
