from django.core.cache.backends.db import DatabaseCache
from django.db import connection, transaction
from django.utils import timezone


class LockedDatabaseCache(DatabaseCache):
    """Share rate limits across workers without a separate cache service."""

    @staticmethod
    def _lock():
        # ponytail: global cache lock; move to a supported Redis cache if traffic grows.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(1936746859)")

    @transaction.atomic
    def add(self, *args, **kwargs):
        self._lock()
        return super().add(*args, **kwargs)

    @transaction.atomic
    def incr(self, key, delta=1, version=None):
        self._lock()
        value = self.get(key, version=version)
        if value is None:
            raise ValueError("Cannot increment a missing rate-limit counter.")
        stored_key = self.make_and_validate_key(key, version=version)
        with connection.cursor() as cursor:
            table = connection.ops.quote_name(self._table)
            cursor.execute(
                f"SELECT expires FROM {table} WHERE cache_key = %s",
                [stored_key],
            )
            expires = cursor.fetchone()[0]
        value += delta
        # BaseCache.incr resets the TTL; hourly limits must retain their window.
        if not self._base_set(
            "set",
            stored_key,
            value,
            (expires - timezone.now()).total_seconds(),
        ):
            raise ValueError("Unable to update the rate-limit counter.")
        return value

    @transaction.atomic
    def get_many(self, *args, **kwargs):
        self._lock()
        return super().get_many(*args, **kwargs)
