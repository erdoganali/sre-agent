"""MCP sunucuları (T3.3, ADR-030): yetenek başına salt okunur araçlar.

Sunucular: metrics (Prometheus), logs (Loki), traces (Tempo), k8s (Kubernetes API), product
(domain paketi). Her biri ayrı süreç ve ServiceAccount ile çalışır (`python -m sre_agent.mcp
<ad>`); testlerde aynı süreçte. Araçlar portları kullanır (ADR-005): adaptör değişir,
araç aynı kalır. Specialist'ler araçlara `client.ToolClient` üzerinden gider (tekrar eden çağrı,
kırpma, güvenilmez veri işareti).
"""
