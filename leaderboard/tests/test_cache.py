import logging
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.test import SimpleTestCase, TransactionTestCase

from leaderboard.logging import RedactTokens


class CacheTests(TransactionTestCase):
    def test_increment_preserves_hourly_expiration(self):
        cache.clear()
        cache.add("hourly", 1, timeout=3600)
        with connection.cursor() as cursor:
            cursor.execute("SELECT expires FROM rate_limit_cache")
            before = cursor.fetchone()[0]
        cache.incr("hourly")
        with connection.cursor() as cursor:
            cursor.execute("SELECT expires FROM rate_limit_cache")
            after = cursor.fetchone()[0]
        self.assertLess(abs((after - before).total_seconds()), 2)
        cache.clear()

    def test_concurrent_increments_are_shared_and_atomic(self):
        cache.clear()
        self.assertTrue(cache.add("counter", 0, timeout=60))

        def increment():
            close_old_connections()
            try:
                for _ in range(10):
                    cache.incr("counter")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: increment(), range(4)))
        self.assertEqual(cache.get("counter"), 40)
        self.assertFalse(cache.add("counter", 0))
        cache.clear()


class LoggingTests(SimpleTestCase):
    def test_secret_paths_are_redacted(self):
        record = logging.LogRecord(
            "django.server", logging.INFO, "", 1, '"GET %s HTTP/1.1"',
            ("/invitations/accept/private-token/",), None,
        )
        RedactTokens().filter(record)
        self.assertNotIn("private-token", record.getMessage())
        record = logging.LogRecord(
            "django.request", logging.ERROR, "", 1,
            "Bad request: /accounts/reset/Mg/private-reset-token/", (), None,
        )
        RedactTokens().filter(record)
        self.assertNotIn("private-reset-token", record.getMessage())
