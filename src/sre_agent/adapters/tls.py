"""TlsPort: TLS el sıkışmasıyla sunucu sertifikasını okur.

Secret okunmaz; yalnızca uç noktaya bağlanılır. Süresi dolmuş ya da kendinden imzalı
sertifikaları da görebilmek için doğrulama kapalıdır (amaç güven değil, bitiş tarihi).
"""

from __future__ import annotations

import asyncio
import ssl
from datetime import UTC

from cryptography import x509

from sre_agent.ports import Certificate


class TlsProbe:
    def __init__(self, timeout_s: float = 5.0):
        self._timeout = timeout_s

    async def certificate(
        self, host: str, port: int, server_name: str | None = None
    ) -> Certificate:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, server_hostname=server_name or host),
            self._timeout,
        )
        try:
            sslobj = writer.get_extra_info("ssl_object")
            der = sslobj.getpeercert(binary_form=True) if sslobj else None
            if not der:
                raise ConnectionError(f"{host}:{port} sertifika göndermedi")
            cert = x509.load_der_x509_certificate(der)
        finally:
            writer.close()
            await writer.wait_closed()
        return Certificate(
            endpoint=f"{host}:{port}",
            subject=cert.subject.rfc4514_string(),
            issuer=cert.issuer.rfc4514_string(),
            not_after=cert.not_valid_after_utc.astimezone(UTC),
        )
