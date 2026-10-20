import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sre_agent.checks.builtin import BUILTIN_CHECKS
from sre_agent.packs import CheckRegistry, Config, PackConfig, load_config, load_pack, load_packs

REPO = Path(__file__).parents[1]


def run_isolated(code: str, cwd: Path) -> str:
    """Ayrı süreçte çalıştırır (önceki import'lar sys.modules'ü kirletmesin)."""
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)], cwd=cwd, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_core_without_packs_does_not_import_any_pack(tmp_path: Path) -> None:
    out = run_isolated(
        """
        import sys
        from pathlib import Path
        from sre_agent.packs import CheckRegistry, Config, load_packs
        reg = CheckRegistry(load_packs(Config(), Path(".")))
        print(len(reg.checks), any(m.startswith("sre_agent_packs") for m in sys.modules))
        """,
        tmp_path,  # packs/ dizini olmayan bir yer: paket kaldırılmış gibi
    )
    assert out == f"{len(BUILTIN_CHECKS)} False"


def test_config_loads_vectordb_pack() -> None:
    config = load_config(REPO / "config" / "gke.yaml")
    reg = CheckRegistry(load_packs(config, REPO))
    assert "milvus_querynode_memory_quota" in reg.checks
    assert len(reg.checks) == len(BUILTIN_CHECKS) + 7
    assert ("milvus.io", "v1beta1", "Milvus", "milvuses") in reg.custom_kinds
    assert any(s.name == "milvus_search_p99_ms" for s in reg.signals)
    assert "milvus_querynode_memory_quota" in reg.for_alert("MilvusQueryNodeMemoryHigh")
    assert reg.for_alert("SomethingElse") == []


def test_missing_pack_path_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_packs(Config(packs=[PackConfig(path="packs/yok")]), tmp_path)


def write_pack(root: Path, body: str, routes: str = "{}") -> Path:
    pack = root / "p"
    (pack / "mod").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        f"name: p\nversion: 0.1.0\nmodule: mod\nalert_routes: {routes}\n"
    )
    (pack / "mod" / "__init__.py").write_text(textwrap.dedent(body))
    return pack


def test_pack_must_export_pack_content(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="PACK"):
        load_pack(write_pack(tmp_path, "PACK = 42\n"))


def test_route_to_unknown_check_is_rejected(tmp_path: Path) -> None:
    body = "from sre_agent.packs import PackContent\nPACK = PackContent()\n"
    pack = load_pack(write_pack(tmp_path, body, routes="{X: [no_such_check]}"))
    with pytest.raises(ValueError, match="bilinmeyen kontroller"):
        CheckRegistry([pack])


def test_duplicate_check_name_is_rejected(tmp_path: Path) -> None:
    body = """
    from sre_agent.checks import Check, Status
    from sre_agent.packs import PackContent
    from sre_agent.ports import Capability

    class Clash(Check):
        name = "pod_restarts"
        capability = Capability.K8S
        categories = ("database",)

        async def run(self, ctx):
            return self.evidence(ctx, Status.OK, "x")

    PACK = PackContent(checks=[Clash])
    """
    with pytest.raises(ValueError, match="çakışıyor"):
        CheckRegistry([load_pack(write_pack(tmp_path, body))])


def test_core_token_expands_to_builtin_checks_plus_pack_checks() -> None:
    from pathlib import Path

    from sre_agent.checks.builtin import BUILTIN_CHECKS
    from sre_agent.config import load_app_config
    from sre_agent.packs import CheckRegistry, load_packs

    root = Path(__file__).parents[1]
    reg = CheckRegistry(load_packs(load_app_config(root / "config/gke.yaml"), root))
    names = reg.for_alert("VectorDBPodRestarting")
    assert {c.name for c in BUILTIN_CHECKS} <= set(names) and "milvus_health" in names
    assert "@core" not in names and reg.packs_for_alert("VectorDBPodCrashLooping") == ["vectordb"]
    assert "node_recreated" not in reg.for_alert(
        "MilvusObjectStorageErrors"
    )  # @core yoksa yalnız liste
