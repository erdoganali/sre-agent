"""Arıza kataloğu (T1.15): bağlantılar, catalog.md güncelliği, kontrol metrikleri."""

from pathlib import Path

from sre_agent import catalog
from sre_agent.packs import CheckRegistry, load_pack

PACK_DIR = Path(__file__).parents[1]
ROOT = PACK_DIR.parents[1]
PACK = load_pack(PACK_DIR)
CATALOG = catalog.load(PACK_DIR)
PREFIXES = ("milvus_", "internal_", "woodpecker_")


def test_links_are_valid() -> None:
    checks = set(CheckRegistry([PACK]).checks)
    assert catalog.problems(CATALOG, PACK_DIR, ROOT, checks) == []


def test_rendered_markdown_is_up_to_date() -> None:
    expected = catalog.render(CATALOG, ROOT)
    assert (PACK_DIR / catalog.RENDERED_FILE).read_text() == expected, "make catalog"


def test_pack_check_metrics_exist_in_inventory() -> None:
    """Paket kontrollerinin sorguladığı ürün metrikleri kurulu sürümde gerçekten var."""
    queries = [q for c in PACK.content.checks for q in c.queries.values()]
    queries += [s.query for s in PACK.content.signals]
    names = catalog.check_metric_names(queries, PREFIXES)
    inventory = catalog.read_inventory(PACK_DIR / CATALOG.inventory)

    def known(name: str) -> bool:  # histogram _count son eki envanterde atılmış
        return name in inventory or (name.endswith("_count") and name[:-6] in inventory)

    unknown = {n for n in names if not known(n)}
    assert unknown == set(CATALOG.unverified_check_metrics)


def test_every_pack_check_is_in_catalog() -> None:
    """Paketin her kontrolü en az bir arızaya bağlı (eksik görünür olsun)."""
    used = {c for e in CATALOG.entries for c in e.checks}
    assert {c.name for c in PACK.content.checks} <= used


def test_missing_links_are_listed() -> None:
    text = catalog.render(CATALOG, ROOT)
    assert "## Eksikler" in text and "Yolda olmayan sinyaller" in text
