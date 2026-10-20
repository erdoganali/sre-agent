# Adding your own product (domain packs)

The core (`src/sre_agent`) knows no product. Checks, the graph, the specialists and the MCP servers are generic; a test (`test_core_does_not_name_a_product`) fails if a product name appears there. Everything product-specific lives in a **pack**: a directory under `packs/` that is loaded from configuration. Several packs can run side by side.

Start from `packs/example/` (a small HTTP API with Postgres). `packs/vectordb/` is the full real-world pack.

## Layout

| File | Required | What it gives the agent |
| --- | --- | --- |
| `pack.yaml` | yes | name, alert → checks routing, Alertmanager matchers, freshness probes, category notes |
| `<module>/__init__.py` | yes | `PACK = PackContent(checks=..., signals=..., custom_kinds=...)` |
| `catalog.yaml` | no | known failures; the knowledge base (RAG) is built from it |
| `specialists/<name>.md` | no | guidance appended to that specialist's prompt (`product`, `k8s`, `metrics`, `logs`, `traces`) |
| `topology.yaml` | no | components, pod-name patterns, dependencies (the product MCP `topology` tool) |
| `rbac-values.yaml` | no | extra read-only RBAC rules (`extraRules`), added by `make rbac-install` |
| `tests/` | no | pack tests, run by `make test` |
| `dashboards/` | no | Grafana dashboards (`make pack-dashboards`) |

## Steps

1. **Copy** `packs/example` to `packs/<yours>`; set `name` and `module` in `pack.yaml` and rename the module directory.
2. **Write checks.** A check is a class with a `name`, a `capability` (metrics, logs, traces, k8s), taxonomy `categories`, and an async `run` that returns `ok`, `anomaly` or `no_data` with a one-line summary. Checks are deterministic: no model. Put product queries in `queries` so an environment can override them.
3. **Route alerts.** In `alert_routes`, map each alert name (regex) to the checks it needs; `"@core"` adds every core check. Add your `alert_matchers` (for example `team="yours"`) to the Alertmanager route that sends to sre-agent.
4. **Describe failures** in `catalog.yaml`: symptom, signals, threshold, alerts and checks that catch it, taxonomy category. This becomes the knowledge base the agent searches.
5. **Guide the specialists** with a few bullets in `specialists/product.md`: what usually causes what in your product.
6. **Map the topology** in `topology.yaml` so the product specialist can see which component depends on which.
7. **Enable** the pack in `config/<env>.yaml`:

   ```yaml
   packs:
     - path: packs/vectordb
     - path: packs/<yours>
   ```

8. **Build the knowledge base:** `python -m sre_agent.knowledge index`.

## Verify

```bash
make test    # also runs packs/*/tests
make lint
```

Copy `packs/example/tests/` with the pack. It checks that every catalog link resolves (checks, alerts, categories, metrics), that the catalog and guides reach the knowledge base, and that the check reads its metric correctly. `tests/test_multi_pack.py` runs two packs side by side: each alert gets only its own pack's checks.

## Limits

- Alert rules are not deployed from the pack yet: `packs/example/alerts.yaml` is only what the catalog links to. Deploy your rules as a PrometheusRule (see `deploy/helm/vectordb-alerts`).
- A pack runs in the agent's process: review its code like core code. It gets the same read-only ports as the core and cannot write.
