# ADR-034: MVP kapsamı (bootcamp teslimi), RAG bilgi tabanı ve etkili soruşturma

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-19
- **Etkilediği faz:** 3, 4

## Bağlam

Proje Agentic AI Bootcamp kapanış projesi olarak 2026-10-30'da GitHub'da teslim edilecek. Değerlendirme uçtan uca çalışmayı, agentic mimariyi (planlama, tool calling, hafıza, çoklu agent, RAG), kod kalitesini ve başka makinede çalıştırılabilirliği ölçüyor. Faz 3'ün kalan işi büyük ölçüde ölçüm (golden set, replay, A–D karşılaştırması, gece koşuları). Bunlar teslim için gerekli değil, ama üç eksik teslimi doğrudan etkiliyor:

- soruşturma sonucu (shadow) hiçbir sayfada görünmüyor;
- RAG yok (Faz 4 planındaydı);
- proje küme olmadan çalışmıyor.

Gateway'de embedding modeli yok; on-prem embedding modeli (`bge-en-icl`) erişilemez.

## Karar

1. **Kapsam:** MVP = alarm → kontrol raporu → çoklu agent soruşturma (MCP araçları) → etkili sonuç raporda. Ertelenenler (MVP sonrası): T1.10 gece koşusu ve golden set v1, ADR-033'ün replay ve A–D karşılaştırması, MCP iyileştirmeleri 3–5, süreç metrikleri, T2.4/T2.6, T3.6'nın tam tehdit modeli, T3.7.
2. **Etkili soruşturma:** `investigation.mode: on` iken graph'ın sonucu (özet, kategori, kök neden, kanıt zinciri, reddedilen hipotezler, öneriler, uzmanlar ve kullandıkları araçlar, token) rapor sayfasının başına yazılır; `shadow`'da sona, "yalnız ölçüm" notuyla.
3. **RAG:** bilgi tabanı paketlerin arıza kataloğu, uzman rehberleri ve taksonomiden repodan üretilir, Milvus'ta tek collection'a yazılır. Arama Milvus 2.6'nın **BM25 full-text** fonksiyonuyla yapılır; embedding modeli gerekmez. İki kullanım:
   - graph'ın `gather` adımı alarm adları ve anomali gören kontrollerden kodda sorgu kurar, en yakın bilinen arızaları brief'e ekler (bütün uzmanlar ve synthesize görür; model karar vermez, ADR-001);
   - product sunucusunda salt okunur `knowledge_search` aracı; uzman gerektiğinde kendisi arar.
   Bilgi tabanı erişilemezse graph yine koşar, hata kayda geçer.
4. **Adaptör:** Milvus istemcisi `adapters/vector_store.py`'de; çekirdek yalnız `KnowledgeStore` portunu görür. "Çekirdek ürün adı içermez" testi bu dosyayı muaf tutar: Milvus burada izlenen ürün değil, bilgi tabanının veritabanı.
5. **Demo kipi:** küme olmadan çalıştırma için bilgi tabanı yerelde Milvus Lite dosyasıyla (aynı kod) çalışır; araç cevapları bir olaydan kayda alınır (ADR-033'ün replay'inin ilk, küçük hâli).
   - Kayıt: `graph run --record F` canlı koşuda her MCP `list_tools`/`call_tool` cevabını ve incident raporlarını yazar (`graph/replay.py`). `demo/incident.json` staging'deki gerçek bir `MilvusEtcdQuorumLost` (node kaybı, 2026-10-20) kaydıdır.
   - Replay: model canlı, tool cevapları kayıttan; birebir kaydı olmayan çağrıya aynı tool'un en yakın kaydı notla döner. Store bellekte, checkpoint yok.
   - `make demo`: `.env`'deki OpenAI uyumlu uç nokta (`.env.example`); anahtar yoksa model çağrılmaz, sonuç kurallarla üretilir ve raporda yazar. Çıktı `demo/out/report.md`.
6. **Ortam:** ana ortam staging cluster (gateway'e oradan erişiliyor).

## Sonuçlar

- Faz 3'ün çıkış ölçütü ("golden set'te baseline'ı geçiyor") MVP'de ölçülmez; teslimde bu açıkça yazılır.
- BM25 anlamsal benzerlik yakalamaz (eş anlamlı, farklı dil). Katalog metni alarm/kontrol adlarını içerdiği için bugünkü sorgularda yeterli; embedding modeli gelince hibrit aramaya geçilir (Faz 4).
- Milvus Lite imaja ~60 MB ekler.
