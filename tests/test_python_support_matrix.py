from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from packaging.markers import Marker, default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _environment(
    *,
    sys_platform: str,
    machine: str,
    release: str,
    python_version: str,
) -> dict[str, str]:
    environment = default_environment()
    environment.update(
        {
            "sys_platform": sys_platform,
            "platform_machine": machine,
            "platform_release": release,
            "python_version": python_version,
            "python_full_version": f"{python_version}.0",
        }
    )
    return environment


SUPPORTED_MACOS_CELLS = (
    *(
        (
            f"arm64-macos14-python{python_version}",
            _environment(
                sys_platform="darwin",
                machine="arm64",
                release="23.0.0",
                python_version=python_version,
            ),
            Version("1.28.0"),
        )
        for python_version in ("3.11", "3.12", "3.13", "3.14")
    ),
    *(
        (
            f"arm64-macos13-python{python_version}",
            _environment(
                sys_platform="darwin",
                machine="arm64",
                release="22.0.0",
                python_version=python_version,
            ),
            Version("1.23.2"),
        )
        for python_version in ("3.11", "3.12", "3.13")
    ),
    *(
        (
            f"x86_64-macos13-python{python_version}",
            _environment(
                sys_platform="darwin",
                machine="x86_64",
                release="22.0.0",
                python_version=python_version,
            ),
            Version("1.23.2"),
        )
        for python_version in ("3.11", "3.12", "3.13")
    ),
)


EXPECTED_PUBLISHED_ORT_REQUIREMENTS = {
    Requirement(
        "onnxruntime>=1.23.2,<1.29; sys_platform == 'darwin' and "
        "platform_machine == 'arm64'"
    ),
    Requirement(
        "onnxruntime==1.23.2; sys_platform == 'darwin' and "
        "platform_machine == 'x86_64'"
    ),
}

EXPECTED_UV_ORT_CONSTRAINTS = {
    Requirement(
        "onnxruntime==1.28.0; sys_platform == 'darwin' and "
        "platform_machine == 'arm64' and platform_release >= '23'"
    ),
    Requirement(
        "onnxruntime==1.23.2; sys_platform == 'darwin' and "
        "platform_machine == 'arm64' and platform_release >= '22' and "
        "platform_release < '23'"
    ),
    Requirement(
        "onnxruntime==1.23.2; sys_platform == 'darwin' and "
        "platform_machine == 'x86_64' and platform_release >= '22'"
    ),
}

HOSTILE_NON_PEP440_RELEASES = (
    ("linux", "x86_64", "6.8.0-45-generic"),
    ("linux", "x86_64", "4.18.0-305.el8.x86_64"),
    ("win32", "AMD64", "2022Server"),
)

EXPECTED_UV_ENVIRONMENTS = {
    Marker("sys_platform != 'darwin'"),
    Marker(
        "sys_platform == 'darwin' and platform_machine == 'arm64' and "
        "platform_release >= '23' and python_version >= '3.11' and "
        "python_version < '3.15'"
    ),
    Marker(
        "sys_platform == 'darwin' and platform_machine == 'arm64' and "
        "platform_release >= '22' and platform_release < '23' and "
        "python_version >= '3.11' and python_version < '3.14'"
    ),
    Marker(
        "sys_platform == 'darwin' and platform_machine == 'x86_64' and "
        "platform_release >= '22' and python_version >= '3.11' and "
        "python_version < '3.14'"
    ),
}

EXPECTED_REQUIRED_ENVIRONMENTS = {
    Marker(
        f"sys_platform == 'darwin' and platform_machine == 'arm64' and "
        f"platform_release >= '23' and python_version == '{python_version}'"
    )
    for python_version in ("3.11", "3.12", "3.13", "3.14")
} | {
    Marker(
        f"sys_platform == 'darwin' and platform_machine == 'arm64' and "
        f"platform_release >= '22' and platform_release < '23' and "
        f"python_version == '{python_version}'"
    )
    for python_version in ("3.11", "3.12", "3.13")
} | {
    Marker(
        f"sys_platform == 'darwin' and platform_machine == 'x86_64' and "
        f"platform_release >= '22' and python_version == '{python_version}'"
    )
    for python_version in ("3.11", "3.12", "3.13")
}


def test_requires_python_preserves_open_ended_code_baseline() -> None:
    raw_specifier = PYPROJECT["project"]["requires-python"]
    specifier = SpecifierSet(raw_specifier)

    assert raw_specifier == ">=3.11"
    assert all(
        Version(python_version) in specifier
        for python_version in ("3.11", "3.12", "3.13", "3.14")
    )
    assert not any(item.operator in {"<", "<="} for item in specifier)


def _ort_requirements(raw_requirements: list[str]) -> list[Requirement]:
    return [
        requirement
        for requirement in map(Requirement, raw_requirements)
        if requirement.name == "onnxruntime"
    ]


def test_published_onnxruntime_requirements_are_architecture_only_and_portable() -> None:
    raw_requirements = PYPROJECT["project"]["dependencies"]
    onnxruntime_requirements = _ort_requirements(raw_requirements)

    assert len(onnxruntime_requirements) == 2
    assert set(onnxruntime_requirements) == EXPECTED_PUBLISHED_ORT_REQUIREMENTS
    assert all(
        "platform_release" not in raw_requirement
        for raw_requirement in raw_requirements
        if Requirement(raw_requirement).name == "onnxruntime"
    )

    # pip 24.2 vendors packaging 24.1, whose eager PEP 508 evaluator attempts
    # to parse every compared platform_release even when sys_platform is false.
    # Published metadata must therefore avoid the comparison entirely so real
    # Linux and Windows release strings cannot abort ``pip install lingtai``.
    for sys_platform, machine, release in HOSTILE_NON_PEP440_RELEASES:
        environment = _environment(
            sys_platform=sys_platform,
            machine=machine,
            release=release,
            python_version="3.13",
        )
        assert not any(
            requirement.marker is not None
            and requirement.marker.evaluate(environment=environment)
            for requirement in onnxruntime_requirements
        )


def test_uv_constraints_select_the_exact_supported_macos_cell_versions() -> None:
    published = _ort_requirements(PYPROJECT["project"]["dependencies"])
    raw_constraints = PYPROJECT["tool"]["uv"].get("constraint-dependencies", [])
    constraints = _ort_requirements(raw_constraints)

    assert len(constraints) == 3, "exact macOS release pins must remain uv-local"
    assert set(constraints) == EXPECTED_UV_ORT_CONSTRAINTS

    for cell_name, environment, expected_version in SUPPORTED_MACOS_CELLS:
        matching_published = [
            requirement
            for requirement in published
            if requirement.marker is not None
            and requirement.marker.evaluate(environment=environment)
        ]
        matching_constraints = [
            requirement
            for requirement in constraints
            if requirement.marker is not None
            and requirement.marker.evaluate(environment=environment)
        ]
        assert len(matching_published) == 1, cell_name
        assert len(matching_constraints) == 1, cell_name
        assert expected_version in matching_published[0].specifier, cell_name
        assert matching_constraints[0].specifier == SpecifierSet(
            f"=={expected_version}"
        ), cell_name


def test_anatomy_managed_runtime_citation_tracks_find_python_definition() -> None:
    source_path = ROOT / "src" / "lingtai" / "venv_resolve.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    definition_lines = [
        node.lineno
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_find_python"
    ]

    assert len(definition_lines) == 1
    expected_citation = f"`venv_resolve.py:_find_python` :{definition_lines[0]}"
    anatomy = (ROOT / "src" / "lingtai" / "ANATOMY.md").read_text(encoding="utf-8")
    assert expected_citation in anatomy


def test_uv_environments_are_exact_disjoint_and_cover_only_supported_space() -> None:
    markers = [Marker(raw) for raw in PYPROJECT["tool"]["uv"]["environments"]]
    assert len(markers) == 4
    assert set(markers) == EXPECTED_UV_ENVIRONMENTS

    supported_and_linux = [
        *((cell_name, environment, True) for cell_name, environment, _ in SUPPORTED_MACOS_CELLS),
        (
            "linux-python3.14",
            _environment(
                sys_platform="linux",
                machine="x86_64",
                release="6.8.0",
                python_version="3.14",
            ),
            True,
        ),
        (
            "arm64-macos13-python3.14",
            _environment(
                sys_platform="darwin",
                machine="arm64",
                release="22.0.0",
                python_version="3.14",
            ),
            False,
        ),
        (
            "x86_64-macos14-python3.14",
            _environment(
                sys_platform="darwin",
                machine="x86_64",
                release="23.0.0",
                python_version="3.14",
            ),
            False,
        ),
    ]

    # Exercise boundary and unsupported cells as well as the ten supported cells.
    for machine in ("arm64", "x86_64"):
        for release in ("21.6.0", "22.0.0", "23.0.0", "24.1.0"):
            for python_version in ("3.10", "3.11", "3.13", "3.14", "3.15"):
                supported_and_linux.append(
                    (
                        f"grid-{machine}-{release}-{python_version}",
                        _environment(
                            sys_platform="darwin",
                            machine=machine,
                            release=release,
                            python_version=python_version,
                        ),
                        None,
                    )
                )

    for cell_name, environment, expected_covered in supported_and_linux:
        matching = [marker for marker in markers if marker.evaluate(environment=environment)]
        assert len(matching) <= 1, f"overlapping uv environments for {cell_name}"
        if expected_covered is not None:
            assert bool(matching) is expected_covered, cell_name


def test_required_environments_explicitly_enumerate_all_ten_macos_abi_cells() -> None:
    markers = [
        Marker(raw)
        for raw in PYPROJECT["tool"]["uv"]["required-environments"]
    ]

    assert len(markers) == 10
    assert set(markers) == EXPECTED_REQUIRED_ENVIRONMENTS
    for cell_name, environment, _expected_version in SUPPORTED_MACOS_CELLS:
        matching = [marker for marker in markers if marker.evaluate(environment=environment)]
        assert len(matching) == 1, cell_name
