"""Yeniden başlatmaya dayanıklı anahtar-değer deposu.

Render'ın ücretsiz katmanında disk kalıcı değildir: servis her yeniden
başladığında dosya sistemi git checkout'una döner. Eski sürüm raporu
``backend/data/report.json`` dosyasına yazdığı için, her restart sonrası site
sessizce depoya en son commit edilmiş (günler öncesine ait) raporu sunuyordu.

Burada rapor ve pahalı ara sonuçlar veritabanına yazılır. ``DATABASE_URL``
tanımlıysa Postgres, değilse yerel SQLite kullanılır. Veritabanı hiç
kullanılamazsa uygulama çökmek yerine dosya sistemine düşer.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

BASE_DIR = Path(__file__).resolve().parent
FALLBACK_DIR = BASE_DIR / "cache"

_metadata = MetaData()

kv_cache = Table(
    "kv_cache",
    _metadata,
    Column("cache_key", String(255), primary_key=True),
    Column("payload", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


# pg8000 bunları kwargs olarak alır ve tanımadığı için TypeError fırlatır.
# Supabase ve Render panelleri bağlantı dizesini sık sık `?sslmode=require`
# ekiyle veriyor; ayıklanmazsa uygulama açılışta çöker.
_LIBPQ_ONLY_PARAMS = {
    "sslmode", "sslcert", "sslkey", "sslrootcert", "sslcrl",
    "channel_binding", "target_session_attrs", "gssencmode", "options",
}


def normalise_database_url(raw: str) -> str:
    """Render/Supabase/Heroku biçimli URL'leri pg8000'in anlayacağı hâle getirir."""
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+pg8000://", 1)

    if "postgresql+pg8000://" in raw and "?" in raw:
        base, _, query = raw.partition("?")
        kept = [
            pair for pair in query.split("&")
            if pair and pair.split("=", 1)[0].lower() not in _LIBPQ_ONLY_PARAMS
        ]
        raw = f"{base}?{'&'.join(kept)}" if kept else base
    return raw


def resolve_database_url() -> str:
    configured = os.environ.get("DATABASE_URL", "").strip()
    if configured:
        return normalise_database_url(configured)
    return f"sqlite:///{BASE_DIR / 'appdata.sqlite'}"


class Store:
    """Basit JSON anahtar-değer deposu; hatada dosya sistemine düşer."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or resolve_database_url()
        self.backend = "postgres" if "postgresql" in self.database_url else "sqlite"
        self._lock = threading.Lock()
        self._engine = None
        self._degraded_reason: str | None = None
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)

    # ── kurulum ────────────────────────────────────────────────────────────
    @property
    def engine(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = self._build_engine()
        return self._engine

    def _build_engine(self):
        options: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if self.backend == "postgres":
            # Render free Postgres bağlantı sayısı düşüktür; havuzu küçük tut
            # ve bayat bağlantıları erken geri dönüştür.
            options.update(pool_size=3, max_overflow=2, pool_recycle=280)
        else:
            options.update(connect_args={"check_same_thread": False})
        engine = create_engine(self.database_url, **options)
        _metadata.create_all(engine)
        return engine

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "persistent": self.backend == "postgres",
            "degraded": self._degraded_reason,
        }

    # ── dosya sistemi yedeği ───────────────────────────────────────────────
    def _fallback_path(self, key: str) -> Path:
        safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in key)
        return FALLBACK_DIR / f"{safe}.json"

    def _fallback_read(self, key: str) -> dict[str, Any] | None:
        path = self._fallback_path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _fallback_write(self, key: str, envelope: dict[str, Any]) -> None:
        path = self._fallback_path(key)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass

    # ── genel API ──────────────────────────────────────────────────────────
    def get(self, key: str) -> Any | None:
        """Değeri döndürür; kayıt yoksa ``None``."""
        envelope = self.get_envelope(key)
        return envelope["value"] if envelope else None

    def get_envelope(self, key: str) -> dict[str, Any] | None:
        """Değeri ``{"value": ..., "updated_at": iso}`` biçiminde döndürür."""
        try:
            with self.engine.connect() as connection:
                row = connection.execute(
                    select(kv_cache.c.payload, kv_cache.c.updated_at).where(kv_cache.c.cache_key == key)
                ).first()
            if row is None:
                return None
            updated_at = row.updated_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            return {
                "value": json.loads(row.payload),
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
        except (SQLAlchemyError, json.JSONDecodeError, OSError) as error:
            self._degraded_reason = f"{type(error).__name__}: {error}"
            return self._fallback_read(key)

    def set(self, key: str, value: Any) -> None:
        now = datetime.now(timezone.utc)
        payload = json.dumps(value, ensure_ascii=False)
        try:
            insert = pg_insert if self.backend == "postgres" else sqlite_insert
            statement = insert(kv_cache).values(cache_key=key, payload=payload, updated_at=now)
            statement = statement.on_conflict_do_update(
                index_elements=[kv_cache.c.cache_key],
                set_={"payload": statement.excluded.payload, "updated_at": statement.excluded.updated_at},
            )
            with self.engine.begin() as connection:
                connection.execute(statement)
        except (SQLAlchemyError, OSError) as error:
            self._degraded_reason = f"{type(error).__name__}: {error}"
            self._fallback_write(key, {"value": value, "updated_at": now.isoformat()})

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Birden çok anahtarı tek sorguda okur (detay önbelleği için)."""
        if not keys:
            return {}
        try:
            found: dict[str, Any] = {}
            with self.engine.connect() as connection:
                # Postgres parametre sınırını aşmamak için parçalara böl.
                for index in range(0, len(keys), 400):
                    chunk = keys[index:index + 400]
                    rows = connection.execute(
                        select(kv_cache.c.cache_key, kv_cache.c.payload).where(kv_cache.c.cache_key.in_(chunk))
                    ).all()
                    for row in rows:
                        try:
                            found[row.cache_key] = json.loads(row.payload)
                        except json.JSONDecodeError:
                            continue
            return found
        except (SQLAlchemyError, OSError) as error:
            self._degraded_reason = f"{type(error).__name__}: {error}"
            result = {}
            for key in keys:
                envelope = self._fallback_read(key)
                if envelope:
                    result[key] = envelope["value"]
            return result

    def set_many(self, items: dict[str, Any]) -> None:
        if not items:
            return
        now = datetime.now(timezone.utc)
        try:
            insert = pg_insert if self.backend == "postgres" else sqlite_insert
            rows = [
                {"cache_key": key, "payload": json.dumps(value, ensure_ascii=False), "updated_at": now}
                for key, value in items.items()
            ]
            with self.engine.begin() as connection:
                for index in range(0, len(rows), 200):
                    chunk = rows[index:index + 200]
                    statement = insert(kv_cache).values(chunk)
                    statement = statement.on_conflict_do_update(
                        index_elements=[kv_cache.c.cache_key],
                        set_={"payload": statement.excluded.payload, "updated_at": statement.excluded.updated_at},
                    )
                    connection.execute(statement)
        except (SQLAlchemyError, OSError) as error:
            self._degraded_reason = f"{type(error).__name__}: {error}"
            for key, value in items.items():
                self._fallback_write(key, {"value": value, "updated_at": now.isoformat()})

    def delete_prefix(self, prefix: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(delete(kv_cache).where(kv_cache.c.cache_key.like(f"{prefix}%")))
        except (SQLAlchemyError, OSError):
            pass


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = Store()
    return _store
