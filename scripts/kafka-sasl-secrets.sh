#!/usr/bin/env bash
# Kafka SASL PLAIN kullanıcılarını (JAAS) ve telemetry-consumer'ın kimliğini Secret olarak oluşturur.
# Şifreler repoya girmez:
#   - VectorDB gateway'i (user1): şifre VectorDB'ın values dosyasından okunur (orada zaten var).
#   - telemetry-consumer: ilk çalıştırmada rastgele üretilir, sonra mevcut Secret'tan korunur.
# Kullanım: scripts/kafka-sasl-secrets.sh <kube-context> <kafka-ns> <consumer-ns> <vectordb-values>
set -euo pipefail

ctx="$1" kafka_ns="$2" consumer_ns="$3" sv_values="$4"
kc() { kubectl --context "$ctx" "$@"; }

sv_user=$(yq '.vectordbOtelCollector.kafka.auth.username' "$sv_values")
sv_pass=$(yq '.vectordbOtelCollector.kafka.auth.password' "$sv_values")
[ -n "$sv_user" ] && [ "$sv_user" != "null" ] || { echo "VectorDB kullanıcı adı bulunamadı: $sv_values"; exit 1; }

consumer_user=telemetry_consumer
consumer_pass=$(kc -n "$consumer_ns" get secret telemetry-consumer-kafka \
  -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || true)
[ -n "$consumer_pass" ] || consumer_pass=$(openssl rand -hex 24)

jaas="org.apache.kafka.common.security.plain.PlainLoginModule required user_${sv_user}=\"${sv_pass}\" user_${consumer_user}=\"${consumer_pass}\";"

kc -n "$kafka_ns" create secret generic kafka-sasl-plain-users \
  --from-literal=jaas.conf="$jaas" --dry-run=client -o yaml | kc apply -f -
kc create namespace "$consumer_ns" --dry-run=client -o yaml | kc apply -f -
kc -n "$consumer_ns" create secret generic telemetry-consumer-kafka \
  --from-literal=username="$consumer_user" --from-literal=password="$consumer_pass" \
  --dry-run=client -o yaml | kc apply -f -
echo "kullanıcılar: $sv_user, $consumer_user"
