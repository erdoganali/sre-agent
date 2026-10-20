# ADR-010: Dil Python; orkestrasyon LangGraph (ADK alternatif olarak açık)

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 3

## Bağlam

Adaylar: Go (tek binary, operator dünyasıyla uyumlu), Python (HolmesGPT'den parça almak kolay), TypeScript (MCP işleriyle aynı stack). Orkestrasyonda LangGraph ve Google ADK değerlendirildi.

## Karar

Dil Python'dur. Orkestrasyon LangGraph ile yapılır; Google ADK alternatif olarak açık tutulur. Agent Substrate'in runtime olarak rolü ayrıca değerlendirilecektir.

## Sonuçlar

- Tipli çıktılar için Pydantic ve structured output, checkpoint'ler için Postgres kullanılır.
- HolmesGPT (Apache 2.0) gibi projelerden kod alınırsa lisans başlığı ve NOTICE korunur.
