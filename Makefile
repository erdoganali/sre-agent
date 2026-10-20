.PHONY: sync lint fmt test template helm-version strimzi-install kafka-install kafka-smoke grafana-admin-secret monitoring-crds monitoring-install loki-install tempo-install lt-smoke kafka-sasl-secrets telemetry-consumer-install telemetry-check ephemeral-metrics-install alerts-install alert-webhook-test rbac-install rbac-check test-integration change-demo image-build image-push grafana-token agent-auth agent-install report-demo dashboards-install pipeline-demo test-db db-secrets db-backup-secret db-install db-backup-now db-restore-test db-restart-test catalog catalog-check dashboards pack-dashboards litmus-scope litmus-check lab-install lab-alerts-install scenarios scenario-inject scenario-cleanup scenario-run scenario-run-all load-image load-install load-run load-traffic-on load-traffic-off model-check model-check-pod decisions-eval decisions-export model-eval-remote investigate demo

KUBE_CONTEXT ?= gke_example-project_us-central1-a_example-gke
ENV ?= gke
KAFKA_NS ?= kafka
KAFKA_CLUSTER ?= telemetry
STRIMZI_CHART ?= oci://quay.io/strimzi-helm/strimzi-kafka-operator
STRIMZI_VERSION ?= 1.2.0
MONITORING_NS ?= monitoring
AGENT_NS ?= sre-agent
AGENT_SA ?= sre-agent-readonly
LAB_NS ?= sre-lab
GCP_PROJECT ?= example-project
BACKUP_BUCKET ?= example-sre-agent-backups
BACKUP_SA ?= sre-agent-backup@$(GCP_PROJECT).iam.gserviceaccount.com
IMAGE ?= registry.example.com/sre-agent-apps/sre-agent
# Etiket: sürüm-commit; commit'lenmemiş değişiklik varsa "-dirty-<içerik özeti>": farklı içerik
# farklı etiket alır (IfNotPresent ile eski image çekilmesin), aynı içerik aynı etiketi.
IMAGE_TAG ?= $(shell grep -m1 '^version' pyproject.toml | cut -d'"' -f2)-$(shell git rev-parse --short HEAD)$(shell \
	if git diff --quiet HEAD && [ -z "$$(git ls-files --others --exclude-standard)" ]; then :; else \
	echo "-dirty-$$( { git diff HEAD; git ls-files --others --exclude-standard | xargs cat 2>/dev/null; } | shasum | cut -c1-7)"; fi)
PROMETHEUS_COMMUNITY_REPO ?= https://prometheus-community.github.io/helm-charts
KPS_VERSION ?= 91.5.3
GRAFANA_COMMUNITY_REPO ?= https://grafana-community.github.io/helm-charts
LOKI_VERSION ?= 18.13.5
TEMPO_VERSION ?= 3.0.0
OTEL_REPO ?= https://open-telemetry.github.io/opentelemetry-helm-charts
OTEL_COLLECTOR_VERSION ?= 0.173.1
EPHEMERAL_REPO ?= https://jmcgrath207.github.io/k8s-ephemeral-storage-metrics/chart
EPHEMERAL_VERSION ?= 1.21.3
VECTORDB_VALUES ?= ../vectordb-platform-helm/values-$(ENV).yaml
# VectorDB topic'lerine düz metin yazılmaz (telemetry-consumer otlp_json bekler).
KAFKA_TOPICS ?= sre_agent_smoke

CHARTS := $(wildcard deploy/helm/*/Chart.yaml)

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src tests packs

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest

template:
	@if [ -z "$(CHARTS)" ]; then echo "deploy/helm altında chart yok, atlanıyor"; fi
	@for c in $(dir $(CHARTS)); do echo "helm lint/template $$c"; \
		v=$$( [ -f $$c/lint-values.yaml ] && echo "-f $$c/lint-values.yaml" ); \
		helm lint $$c $$v > /dev/null && helm template $$c $$v > /dev/null || exit 1; done

# Helm 3.18.0 sıfır değerli if koşullarını yanlış render ediyor (kube-prometheus-stack bozuluyor).
helm-version:
	@v=$$(helm version --template '{{.Version}}'); \
	if [ "$$v" = "v3.18.0" ]; then echo "Helm $$v desteklenmiyor, >= v3.18.1 gerekli (brew upgrade helm)"; exit 1; fi

strimzi-install: helm-version
	helm upgrade --install strimzi-operator $(STRIMZI_CHART) --version $(STRIMZI_VERSION) \
		--kube-context $(KUBE_CONTEXT) -n $(KAFKA_NS) --create-namespace \
		-f deploy/values/$(ENV)/strimzi-operator.yaml --wait

kafka-install: helm-version
	helm upgrade --install kafka deploy/helm/kafka \
		--kube-context $(KUBE_CONTEXT) -n $(KAFKA_NS) \
		-f deploy/values/$(ENV)/kafka.yaml
	kubectl --context $(KUBE_CONTEXT) -n $(KAFKA_NS) wait kafka/$(KAFKA_CLUSTER) --for=condition=Ready --timeout=10m
	kubectl --context $(KUBE_CONTEXT) -n $(KAFKA_NS) wait kafkatopic -l strimzi.io/cluster=$(KAFKA_CLUSTER) --for=condition=Ready --timeout=2m

kafka-smoke:
	scripts/kafka-smoke.sh $(KUBE_CONTEXT) $(KAFKA_NS) $(KAFKA_CLUSTER) $(KAFKA_TOPICS)

KPS_ARGS = monitoring kube-prometheus-stack --repo $(PROMETHEUS_COMMUNITY_REPO) --version $(KPS_VERSION) -n $(MONITORING_NS) \
	-f deploy/values/$(ENV)/kube-prometheus-stack.yaml

# Grafana admin Secret'ı (bir kez). Şifre rastgele üretilir, repoya girmez.
grafana-admin-secret:
	kubectl --context $(KUBE_CONTEXT) create namespace $(MONITORING_NS) --dry-run=client -o yaml \
		| kubectl --context $(KUBE_CONTEXT) apply -f -
	kubectl --context $(KUBE_CONTEXT) -n $(MONITORING_NS) create secret generic grafana-admin \
		--from-literal=admin-user=admin --from-literal=admin-password="$$(openssl rand -base64 24)"

# Helm mevcut CRD'leri güncellemez; chart'ın CRD'leri server-side apply ile uygulanır.
monitoring-crds:
	helm show crds kube-prometheus-stack --repo $(PROMETHEUS_COMMUNITY_REPO) --version $(KPS_VERSION) \
		| kubectl --context $(KUBE_CONTEXT) apply --server-side --force-conflicts -f -

monitoring-install: helm-version
	helm upgrade --install $(KPS_ARGS) --kube-context $(KUBE_CONTEXT) --skip-crds --wait --timeout 10m

loki-install: helm-version
	helm upgrade --install loki loki --repo $(GRAFANA_COMMUNITY_REPO) --version $(LOKI_VERSION) \
		--kube-context $(KUBE_CONTEXT) -n $(MONITORING_NS) -f deploy/values/$(ENV)/loki.yaml --wait --timeout 10m

tempo-install: helm-version
	helm upgrade --install tempo tempo --repo $(GRAFANA_COMMUNITY_REPO) --version $(TEMPO_VERSION) \
		--kube-context $(KUBE_CONTEXT) -n $(MONITORING_NS) -f deploy/values/$(ENV)/tempo.yaml --wait --timeout 10m

lt-smoke:
	scripts/lt-smoke.sh $(KUBE_CONTEXT) $(MONITORING_NS)

# Kafka SASL PLAIN kullanıcıları + telemetry-consumer kimliği (şifreler repoya girmez).
kafka-sasl-secrets:
	scripts/kafka-sasl-secrets.sh $(KUBE_CONTEXT) $(KAFKA_NS) $(MONITORING_NS) $(VECTORDB_VALUES)

telemetry-consumer-install: helm-version
	helm upgrade --install telemetry-consumer opentelemetry-collector --repo $(OTEL_REPO) \
		--version $(OTEL_COLLECTOR_VERSION) --kube-context $(KUBE_CONTEXT) -n $(MONITORING_NS) \
		-f deploy/values/$(ENV)/telemetry-consumer.yaml --wait --timeout 5m

# VectorDB metrik ve loglarının Prometheus/Loki'ye, consumer lag'inin sıfıra yakın olduğunu kontrol eder.
telemetry-check:
	scripts/telemetry-check.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(KAFKA_NS) $(KAFKA_CLUSTER)

# Pod başına ephemeral storage kullanımı (GKE'de cAdvisor vermiyor; ADR-016).
ephemeral-metrics-install: helm-version
	helm upgrade --install ephemeral-storage-metrics k8s-ephemeral-storage-metrics --repo $(EPHEMERAL_REPO) \
		--version $(EPHEMERAL_VERSION) --kube-context $(KUBE_CONTEXT) -n $(MONITORING_NS) \
		-f deploy/values/$(ENV)/ephemeral-storage-metrics.yaml --wait --timeout 5m

# VectorDB alarm kuralları (docs/alarmlar.md). Pod'lar app.kubernetes.io/part-of=vectordb
# etiketiyle seçilir; tenant listesi yok. Ortama özel eşik varsa deploy/values/<ENV>/vectordb-alerts.yaml.
alerts-install: helm-version
	helm upgrade --install vectordb-alerts deploy/helm/vectordb-alerts \
		--kube-context $(KUBE_CONTEXT) -n $(MONITORING_NS) \
		$(if $(wildcard deploy/values/$(ENV)/vectordb-alerts.yaml),-f deploy/values/$(ENV)/vectordb-alerts.yaml)

# Gruplama ve inhibition'ı sentetik alarmlarla doğrular.
alert-webhook-test:
	scripts/alert-webhook-test.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(AGENT_NS)

# SRE agent'ın read-only ServiceAccount'u (get/list/watch, secrets hariç). Kapsam (cluster /
# namespaces, ADR-025) ortamın deploy/values/<ENV>/sre-agent-rbac.yaml dosyasından; yoksa cluster.
# Yapılandırmadaki (config/$(ENV).yaml) paketlerin rbac-values.yaml dosyaları da eklenir.
PACK_RBAC_VALUES = $(foreach p,$(shell yq '.packs[].path' config/$(ENV).yaml 2>/dev/null),\
	$(if $(wildcard $(p)/rbac-values.yaml),-f $(p)/rbac-values.yaml))

rbac-install: helm-version
	helm upgrade --install sre-agent-rbac deploy/helm/sre-agent-rbac \
		--kube-context $(KUBE_CONTEXT) -n $(AGENT_NS) --create-namespace $(PACK_RBAC_VALUES) \
		$(if $(wildcard deploy/values/$(ENV)/sre-agent-rbac.yaml),-f deploy/values/$(ENV)/sre-agent-rbac.yaml)

rbac-check:
	scripts/rbac-check.sh $(KUBE_CONTEXT) $(AGENT_NS) $(AGENT_SA)
	scripts/rbac-check-mcp.sh $(KUBE_CONTEXT) $(AGENT_NS)

# Adaptörlerin GKE'ye karşı read-only entegrasyon testleri.
test-integration:
	scripts/test-integration.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(AGENT_NS) $(AGENT_SA)

# T1.3 kabulü: sre-lab-app'e helm upgrade + kubectl edit uygular (YAZMA), değişiklik
# kontrollerini read-only SA ile koşup raporu yazdırır.
change-demo: helm-version
	scripts/change-demo.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(AGENT_NS) $(AGENT_SA) $(LAB_NS)

# Servis image'ı (GKE node'ları linux/amd64).
image-build:
	docker buildx build --platform linux/amd64 -t $(IMAGE):$(IMAGE_TAG) --load .

# Önce: gcloud auth configure-docker us-central1-docker.pkg.dev
image-push:
	docker push $(IMAGE):$(IMAGE_TAG)

# Grafana anotasyonu için Editor servis hesabı token'ı → sre-agent/sre-agent-grafana Secret'ı (ADR-022).
grafana-token:
	scripts/grafana-token.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(AGENT_NS) sre-agent-grafana

# Rapor ingress'i için basic auth (kullanıcı: sre, şifre rastgele; bir kez). Şifreyi görmek için:
#   kubectl -n $(AGENT_NS) get secret sre-agent-basic-auth-plain -o jsonpath='{.data.password}' | base64 -d
agent-auth:
	@kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) get secret sre-agent-basic-auth >/dev/null 2>&1 \
		&& echo "sre-agent-basic-auth zaten var" || { \
		pw=$$(openssl rand -hex 16); \
		kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) create secret generic sre-agent-basic-auth \
			--from-literal=auth="sre:$$(openssl passwd -apr1 "$$pw")" && \
		kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) create secret generic sre-agent-basic-auth-plain \
			--from-literal=username=sre --from-literal=password="$$pw"; }

agent-install: helm-version
	helm upgrade --install sre-agent deploy/helm/sre-agent --kube-context $(KUBE_CONTEXT) \
		-n $(AGENT_NS) --set image.tag=$(IMAGE_TAG) --set-file config=config/$(ENV).yaml \
		--set-file models=config/models.yaml \
		-f deploy/values/$(ENV)/sre-agent.yaml --wait --timeout 5m

# T1.7 kabulü: sentetik alarm → rapor → Grafana anotasyonu (≤ 60 sn).
report-demo:
	scripts/report-demo.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(AGENT_NS)

# Platform / telemetri hattı dashboard'u (GKE, Prometheus'a bağlı).
dashboards-install: helm-version
	helm upgrade --install platform-dashboards deploy/helm/platform-dashboards \
		--kube-context $(KUBE_CONTEXT) -n $(MONITORING_NS)

# T1.14 kabulü: consumer durdurulur → hat kopukluğu, tenant veri-yok alarmı, kör noktalı rapor.
pipeline-demo:
	scripts/pipeline-demo.sh $(KUBE_CONTEXT) $(MONITORING_NS) $(AGENT_NS)

# Rapor deposu testleri: geçici docker Postgres (postgres:18.6) açar, testten sonra kaldırır.
test-db:
	@id=$$(docker run -d --rm -e POSTGRES_PASSWORD=test -e POSTGRES_DB=sre_agent -p 127.0.0.1::5432 postgres:18.6); \
	port=$$(docker port $$id 5432 | head -1 | cut -d: -f2); \
	for i in $$(seq 1 60); do docker exec $$id psql -U postgres -d sre_agent -tAc 'select 1' >/dev/null 2>&1 && break; sleep 1; done; sleep 1; \
	SRE_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:$$port/sre_agent \
		uv run pytest -m db -o addopts="" -q tests/test_store_postgres.py; rc=$$?; \
	docker rm -f $$id >/dev/null; exit $$rc

# ---------------------------------------------------------------- rapor deposu (T1.12, ADR-023)

# Postgres şifreleri → Secret'lar (bir kez; repoya girmez).
db-secrets:
	scripts/db-secrets.sh $(KUBE_CONTEXT) $(AGENT_NS) $(MONITORING_NS)

# Yedekleme hesabının HMAC anahtarı → postgres-backup-s3 Secret'ı (ekrana basılmaz).
db-backup-secret:
	scripts/db-backup-secret.sh $(KUBE_CONTEXT) $(AGENT_NS) $(GCP_PROJECT) $(BACKUP_SA)

db-install: helm-version
	helm upgrade --install postgres deploy/helm/postgres --kube-context $(KUBE_CONTEXT) \
		-n $(AGENT_NS) -f deploy/values/$(ENV)/postgres.yaml --wait --timeout 5m

# Yedeği hemen al (CronJob'dan elle Job).
db-backup-now:
	kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) create job postgres-backup-manual-$$(date +%s) \
		--from=cronjob/postgres-backup
	@echo "izlemek için: kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) get jobs"

# Kabul: son yedek geçici veritabanına yüklenir, rapor sayısı karşılaştırılır.
db-restore-test:
	scripts/db-restore-test.sh $(KUBE_CONTEXT) $(AGENT_NS) $(BACKUP_BUCKET)

# Kabul: sre-agent ve Postgres yeniden başlatılır, raporların durduğu doğrulanır.
db-restart-test:
	scripts/db-restart-test.sh $(KUBE_CONTEXT) $(AGENT_NS)

# ---------------------------------------------------------------- arıza kataloğu (T1.15)

PROMETHEUS_URL ?= http://prometheus.example.internal
CATALOG_PACK ?= packs/vectordb

# catalog.yaml → catalog.md
catalog:
	uv run python -m sre_agent.catalog render $(CATALOG_PACK)

# Katalogdaki metrikler beyan edilen yolda canlıda görülüyor mu (salt okunur)
catalog-check:
	uv run python -m sre_agent.catalog verify $(CATALOG_PACK) --prometheus $(PROMETHEUS_URL)

# ---------------------------------------------------------------- paket dashboard'ları (T1.13)

# Paket dashboard'larını sorgu dosyalarından üretir (packs/*/dashboards/build.py)
dashboards:
	@for b in packs/*/dashboards/build.py; do uv run python $$b; done

# Yapılandırmadaki paketlerin dashboard'ları → Grafana (pack-dashboards chart'ı, paket başına bir release)
pack-dashboards: helm-version
	@for p in $$(yq '.packs[].path' config/$(ENV).yaml); do \
		name=$$(basename $$p); files=""; \
		for f in $$p/dashboards/*.json; do [ -f "$$f" ] && files="$$files --set-file dashboards.$$(basename $$f .json)=$$f"; done; \
		[ -n "$$files" ] || continue; \
		echo "== $$name"; \
		helm upgrade --install dashboards-$$name deploy/helm/pack-dashboards --kube-context $(KUBE_CONTEXT) \
			-n $(MONITORING_NS) --set folder=$$name $$files || exit 1; \
	done

# ---------------------------------------------------------------- Litmus (T1.8, ADR-026)

LITMUS_NS ?= litmus
LITMUS_SCOPE_VALUES = deploy/values/$(ENV)/litmus-scope.yaml

# Litmus ajanının yazma yetkisini listelenen namespace'lerle sınırlar; ajanın küme çapındaki bağlarını siler.
# Ajan ChaosCenter'dan yeniden kurulursa/güncellenirse tekrar çalıştırılır.
litmus-scope: helm-version
	helm upgrade --install litmus-scope deploy/helm/litmus-scope --kube-context $(KUBE_CONTEXT) \
		-n $(LITMUS_NS) -f $(LITMUS_SCOPE_VALUES)
	kubectl --context $(KUBE_CONTEXT) delete clusterrolebinding --ignore-not-found \
		$$(yq '.removeClusterRoleBindings[]' deploy/helm/litmus-scope/values.yaml)
	scripts/litmus-check.sh $(KUBE_CONTEXT) $(LITMUS_NS) $(LITMUS_SCOPE_VALUES)

litmus-check:
	scripts/litmus-check.sh $(KUBE_CONTEXT) $(LITMUS_NS) $(LITMUS_SCOPE_VALUES)

# ---------------------------------------------------------------- chaos senaryoları (T1.8)

# Deney uygulaması, chart varsayılanlarıyla (senaryoların temel hâli)
lab-install: helm-version
	helm upgrade --install sre-lab-app deploy/helm/sre-lab-app --kube-context $(KUBE_CONTEXT) \
		-n $(LAB_NS) --create-namespace --reset-values --force-conflicts --wait

# Deney uygulamasının kısa süreli alarm kuralları (team=sre-lab → sre-agent)
lab-alerts-install: helm-version
	helm upgrade --install sre-lab-alerts deploy/helm/sre-lab-alerts --kube-context $(KUBE_CONTEXT) \
		-n $(MONITORING_NS)

scenarios:
	@for d in scenarios/[0-9]*/; do printf '%-26s %s\n' "$$(basename $$d)" "$$(yq '.title' $$d/expected.yaml)"; done

# make scenario-inject S=03-image-pull  /  make scenario-cleanup S=03-image-pull
scenario-inject:
	@test -n "$(S)" || { echo "S=<senaryo> gerekli (make scenarios)"; exit 1; }
	scripts/scenario.sh inject $(S) $(KUBE_CONTEXT)

scenario-cleanup:
	@test -n "$(S)" || { echo "S=<senaryo> gerekli (make scenarios)"; exit 1; }
	scripts/scenario.sh cleanup $(S) $(KUBE_CONTEXT)

# Senaryo koşucusu (T1.9): enjekte → alarm → rapor → karşılaştır → etiketle → temizle; sonuç eval/results/
SETTLE_MIN ?= 30
scenario-run:
	@test -n "$(S)" || { echo "S=<senaryo> gerekli (make scenarios)"; exit 1; }
	uv run python -m sre_agent.eval run $(S) --context $(KUBE_CONTEXT) --settle-min 0

# Hepsi; yük gerektirenler (requires: load) atlanır. Aradaki bekleme rapor penceresi kadar (30 dk).
scenario-run-all:
	uv run python -m sre_agent.eval run --context $(KUBE_CONTEXT) --settle-min $(SETTLE_MIN)

# Milvus yük üreticisi (loadtest tenant'ı; senaryo 06, 07, 10). Bağlantı Secret'ı milvus-load repoya girmez.
LOAD_NS ?= loadtest
LOAD_IMAGE ?= registry.example.com/sre-agent-apps/milvus-load
LOAD_TAG ?= 0.1.3
LOAD_ROWS ?= 2100000

load-image:
	docker buildx build --platform linux/amd64 -t $(LOAD_IMAGE):$(LOAD_TAG) --push tools/milvus-load

load-install: helm-version
	helm upgrade --install milvus-load deploy/helm/milvus-load --kube-context $(KUBE_CONTEXT) -n $(LOAD_NS) \
		--set image.tag=$(LOAD_TAG) --set rows=$(LOAD_ROWS) --reset-values --force-conflicts

# Tek seferlik eylem: make load-run A=fill|load|release|status|drop (bitene kadar günlüğü izler)
load-run:
	@test -n "$(A)" || { echo "A=fill|load|release|status|drop gerekli"; exit 1; }
	@j=milvus-load-$(A)-$$(date +%H%M%S); \
	kubectl --context $(KUBE_CONTEXT) -n $(LOAD_NS) create job $$j --from=cronjob/milvus-load-$(A) >/dev/null && \
	kubectl --context $(KUBE_CONTEXT) -n $(LOAD_NS) wait --for=condition=Ready pod -l job-name=$$j --timeout=180s >/dev/null 2>&1; \
	kubectl --context $(KUBE_CONTEXT) -n $(LOAD_NS) logs -f job/$$j; \
	kubectl --context $(KUBE_CONTEXT) -n $(LOAD_NS) wait --for=condition=Complete job/$$j --timeout=10s >/dev/null

load-traffic-on:
	kubectl --context $(KUBE_CONTEXT) -n $(LOAD_NS) scale deploy/milvus-load-traffic --replicas=1
load-traffic-off:
	kubectl --context $(KUBE_CONTEXT) -n $(LOAD_NS) scale deploy/milvus-load-traffic --replicas=0

# Model kataloğundaki rolleri gerçek uç noktaya karşı sınar (T2.1). Modele erişen bir ağdan:
#   make model-check            # bu makineden
#   make model-check-pod        # kümedeki sre-agent pod'undan (GKE → model yolu gerekir)
model-check:
	uv run python -m sre_agent.llm check
model-check-pod:
	kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) exec deploy/sre-agent -- \
		python -m sre_agent.llm check --catalog /etc/sre-agent/models.yaml

# Etiketli raporlarda karar ölçümü (T2.5/T2.6): pod içinde, DATABASE_URL pod'da (parola ekrana gelmez)
# SAVE=1: kararları kaydet (karar katmanından önceki etiketli raporlar için geriye dönük)
decisions-eval:
	kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) exec deploy/sre-agent -- python -m sre_agent.decisions evaluate $(if $(SAVE),--save)

# Modele erişen başka bir kümede ölçüm (T2.1/T2.4/T2.6). Veri: make decisions-export.
EVAL_CONTEXT ?= gke_example-project_us-central1-a_example-staging
EVAL_NS ?= sre-agent-eval
EVAL_DATA ?= eval/datasets/etiketli.json
EVAL_MODEL_ENV ?= QWEN3_6_35B_URL=http://vllm.example.internal:8520/v1/chat/completions GPT_OSS_20B_URL=http://vllm.example.internal:8502/v1/chat/completions
# Specialist ölçümü (T3.4): EAG_KEY ortamda; veri decisions-export dosyası. INC= ile olay seçilir.
specialists-eval:
	scripts/specialists-eval.sh $(KUBE_CONTEXT) $(EVAL_DATA) $(if $(INC),--incidents $(INC)) \
		--out eval/results/$$(date -u +%Y%m%dT%H%MZ)-specialists.json

# Pod içinde arka planda dosyaya yazılır, hazır olunca kopyalanır (uzun exec akışı kesiliyordu).
decisions-export:
	scripts/decisions-export.sh $(KUBE_CONTEXT) $(AGENT_NS) $(EVAL_DATA)

# Investigation graph'ı bir incident üzerinde elle koştur (T3.2): make investigate INC=<incident-id>
investigate:
	@test -n "$(INC)" || { echo "INC=<incident-id> gerekli"; exit 1; }
	kubectl --context $(KUBE_CONTEXT) -n $(AGENT_NS) exec deploy/sre-agent -- python -m sre_agent.graph run --incident $(INC)

# Demo mode (ADR-034): küme olmadan kayıtlı incident üzerinde investigation → demo/out/report.md
demo:
	uv run python -m sre_agent.demo
