"""Milvus yük üreticisi: senaryolar için gerçek veri ve trafik (T1.8/T1.10).

Eylemler (ilk argüman):
  fill     collection'ı açar, N vektör yazar, index'ler (yüklemez)
  load     collection'ı querynode belleğine yükler (bellek yükselir)
  release  collection'ı bellekten çıkarır (bellek düşer)
  traffic  ayrı küçük collection'a sürekli yazma + arama (object storage trafiği)
  status   collection, satır sayısı, yükleme durumu
  drop     collection'ı siler

Bağlantı: MILVUS_URI, MILVUS_USER, MILVUS_PASSWORD (Secret'tan). Collection: LOAD_COLLECTION.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np
from pymilvus import DataType, MilvusClient

COLLECTION = os.environ.get("LOAD_COLLECTION", "sre_load")
DIM = int(os.environ.get("LOAD_DIM", "768"))
ROWS = int(os.environ.get("LOAD_ROWS", "200000"))  # toplam; parçalara eşit bölünür
# Veri bu kadar collection'a bölünür. Tek collection'da compaction segmentleri ~1 GB'a
# birleştirir, HNSW derlemesi datanode'u (2 GiB) OOMKilled eder (2026-10-08).
# Collection başına ≤ ~500 MB kalmalı.
SHARDS = int(os.environ.get("LOAD_SHARDS", "1"))
BATCH = int(os.environ.get("LOAD_BATCH", "2000"))
# HNSW index querynode belleğine yüklenir; FLAT/ham vektörü Milvus 2.6 mmap ile diskten okur.
INDEX = os.environ.get("LOAD_INDEX", "HNSW")
INDEX_PARAMS = {"HNSW": {"M": 16, "efConstruction": 64}}
TRAFFIC_COLLECTION = os.environ.get("LOAD_TRAFFIC_COLLECTION", "sre_load_traffic")
TRAFFIC_INTERVAL_S = float(os.environ.get("LOAD_TRAFFIC_INTERVAL_S", "1"))


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def client() -> MilvusClient:
    return MilvusClient(
        uri=os.environ["MILVUS_URI"],
        user=os.environ["MILVUS_USER"],
        password=os.environ["MILVUS_PASSWORD"],
        timeout=60,
    )


def vectors(n: int) -> list[list[float]]:
    v = np.random.random((n, DIM)).astype(np.float32)
    return v.tolist()


def ensure(c: MilvusClient, name: str) -> None:
    if not c.has_collection(name):
        schema = c.create_schema(auto_id=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("vec", DataType.FLOAT_VECTOR, dim=DIM)
        index = c.prepare_index_params()
        index.add_index(
            "vec", index_type=INDEX, metric_type="L2", params=INDEX_PARAMS.get(INDEX, {})
        )
        c.create_collection(name, schema=schema, index_params=index)
        log(f"collection açıldı: {name} (dim={DIM}, index={INDEX})")
        c.release_collection(name)


def shards() -> list[str]:
    return [COLLECTION] if SHARDS == 1 else [f"{COLLECTION}_{i:02d}" for i in range(SHARDS)]


def fill(c: MilvusClient) -> None:
    target = ROWS // len(shards())
    t0, done = time.time(), 0
    for name in shards():
        ensure(c, name)
        have = int(c.get_collection_stats(name)["row_count"])
        while have < target:
            n = min(BATCH, target - have)
            c.insert(name, [{"vec": v} for v in vectors(n)])
            have += n
            done += n
        c.flush(name)
        log(f"{name}: {have} satır ({done / max(time.time() - t0, 1):.0f}/sn)")
    log(f"flush tamam: {len(shards())} collection x {target} satır")


def status(c: MilvusClient) -> None:
    names = [n for n in shards() if c.has_collection(n)]
    if not names:
        log("collection yok")
        return
    rows = sum(int(c.get_collection_stats(n)["row_count"]) for n in names)
    states = {str(c.get_load_state(n)["state"]) for n in names}
    pending = sum(int(c.describe_index(n, "vec").get("pending_index_rows") or 0) for n in names)
    log(
        f"{len(names)} collection: satır={rows}, index bekleyen={pending}, yükleme={sorted(states)}"
    )


def traffic(c: MilvusClient) -> None:
    ensure(c, TRAFFIC_COLLECTION)
    c.load_collection(TRAFFIC_COLLECTION)
    log(f"trafik başladı ({TRAFFIC_COLLECTION}): her {TRAFFIC_INTERVAL_S:g} sn 50 yazma + 1 arama")
    i = 0
    while True:
        try:
            c.insert(TRAFFIC_COLLECTION, [{"vec": v} for v in vectors(50)])
            c.search(TRAFFIC_COLLECTION, data=vectors(1), limit=5)
            i += 1
            if i % 60 == 0:
                c.flush(TRAFFIC_COLLECTION)
                log(f"trafik: {i} tur")
        except Exception as exc:  # trafik hatası senaryonun beklenen etkisi olabilir
            log(f"trafik hatası: {type(exc).__name__}: {str(exc)[:200]}")
        time.sleep(TRAFFIC_INTERVAL_S)


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    c = client()
    if action == "fill":
        fill(c)
    elif action == "load":
        t0 = time.time()
        for name in shards():
            c.load_collection(name)
        log(f"yüklendi: {len(shards())} collection ({time.time() - t0:.0f} sn)")
    elif action == "release":
        for name in shards():
            c.release_collection(name)
        log("bellekten çıkarıldı")
    elif action == "traffic":
        traffic(c)
    elif action == "drop":
        for name in [*shards(), COLLECTION, TRAFFIC_COLLECTION]:
            c.drop_collection(name)
        log("silindi")
    else:
        status(c)
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
