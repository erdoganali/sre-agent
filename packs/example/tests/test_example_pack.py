"""Pack tests: copy this file with the pack. `make test` runs every `packs/*/tests`."""

from pathlib import Path

from fakes import WINDOW, FakeMetrics
from sre_agent import catalog, knowledge
from sre_agent.checks import CheckContext, Status
from sre_agent.packs import CheckRegistry, load_pack
from sre_agent.ports import Capability, Sample

PACK_DIR = Path(__file__).parents[1]
ROOT = PACK_DIR.parents[1]
PACK = load_pack(PACK_DIR)


def test_catalog_links_are_valid() -> None:
    checks = set(CheckRegistry([PACK]).checks)
    assert catalog.problems(catalog.load(PACK_DIR), PACK_DIR, ROOT, checks) == []


def test_catalog_and_guide_reach_the_knowledge_base() -> None:
    docs = knowledge.collect({PACK.manifest.name: PACK_DIR})
    assert {d.id for d in docs if d.pack == PACK.manifest.name} == {
        "example:failure:bad-rollout",
        "example:failure:db-pool-exhausted",
        "example:guide:product",
    }
    assert PACK.topology is not None and set(PACK.guides) == {"product"}


async def test_error_rate_check() -> None:
    (check_cls,) = PACK.content.checks
    for ratio, want in ((0.2, Status.ANOMALY), (0.01, Status.OK)):
        metrics = FakeMetrics({check_cls.queries["ratio"].format(ns="orders"): [Sample({}, ratio)]})
        ports = {Capability.METRICS: metrics}
        ctx = CheckContext(labels={"namespace": "orders"}, window=WINDOW, ports=ports)
        assert (await check_cls().run(ctx)).status is want
