"""Bilgi tabanının vektör veritabanı adaptörü: Milvus (BM25 full-text, embedding yok).

Aynı kod küme içindeki Milvus'a (`http://...:19530`) ve yerel Milvus Lite dosyasına
(`./knowledge.db`) bağlanır. Tek collection: id, kind, title, category, pack, body, text (+ sparse).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sre_agent.knowledge import TEXT_MAX, Doc, Hit, KnowledgeConfig, query_text


class KnowledgeBase:
    """`KnowledgeStore` portunun Milvus uygulaması."""

    def __init__(self, cfg: KnowledgeConfig, client: Any = None):
        if not cfg.uri and client is None:
            raise ValueError("knowledge.uri yok")
        self.cfg = cfg
        self._client = client
        self._loaded = False

    @property
    def client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            assert self.cfg.uri
            self._client = MilvusClient(self.cfg.uri, token=os.environ.get(self.cfg.token_env, ""))
        return self._client

    def rebuild(self, docs: list[Doc]) -> int:
        """Collection silinip baştan yazılır (kaynak repo; kısmi güncelleme yok)."""
        from pymilvus import DataType, Function, FunctionType

        c, name = self.client, self.cfg.collection
        if c.has_collection(name):
            c.drop_collection(name)
        s = c.create_schema()
        s.add_field("id", DataType.VARCHAR, is_primary=True, max_length=200)
        for f, n in (("kind", 20), ("category", 64), ("pack", 64), ("title", 500)):
            s.add_field(f, DataType.VARCHAR, max_length=n)
        s.add_field("body", DataType.VARCHAR, max_length=TEXT_MAX * 3)  # gösterilen metin
        # Aranan metin: gövde + alarm/kontrol adları bölünmüş hâli (MilvusEtcdQuorumLost → etcd)
        s.add_field("text", DataType.VARCHAR, max_length=TEXT_MAX * 6, enable_analyzer=True)
        s.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        s.add_function(
            Function(
                name="bm25",
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["sparse"],
            )
        )
        ip = c.prepare_index_params()
        ip.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        c.create_collection(name, schema=s, index_params=ip)
        rows = [
            {
                "id": d.id,
                "kind": d.kind,
                "category": d.category,
                "pack": d.pack,
                "title": d.title[:500],
                "body": d.text[:TEXT_MAX],
                "text": d.text[:TEXT_MAX] + "\n" + query_text(d.text[:TEXT_MAX]),
            }
            for d in docs
        ]
        c.insert(name, rows)
        c.load_collection(name)
        return len(rows)

    def search_sync(self, query: str, limit: int = 5, kind: str | None = None) -> list[Hit]:
        q = query_text(query).strip()
        if not q:
            return []
        if not self._loaded:  # yeni süreçte collection bellekte değil (Milvus Lite dahil)
            self.client.load_collection(self.cfg.collection)
            self._loaded = True
        res = self.client.search(
            self.cfg.collection,
            data=[q],
            anns_field="sparse",
            limit=limit,
            filter=f'kind == "{kind}"' if kind else "",
            output_fields=["kind", "title", "category", "pack", "body"],
        )
        hits = []
        for r in res[0] if res else []:
            e = r["entity"]
            doc = Doc(r["id"], e["kind"], e["title"], e["category"], e["body"], e["pack"])
            hits.append(Hit(doc, abs(float(r["distance"]))))
        return hits

    async def search(self, query: str, limit: int = 5, kind: str | None = None) -> list[Hit]:
        return await asyncio.to_thread(self.search_sync, query, limit, kind)
