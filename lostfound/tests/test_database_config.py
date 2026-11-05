"""Tests for database configuration, DATABASE_URL parsing, PostgreSQL-only enforcement,
and credential redaction in startup checks."""
import unittest
from unittest import mock

from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.test import SimpleTestCase

from config.settings import _postgres_from_url, _resolve_sslmode
from lostfound import checks


class ResolveSSLModeTests(SimpleTestCase):
    def test_localhost_keeps_ssl_disabled(self):
        db = {"HOST": "localhost", "SSLMODE": None}
        self.assertIsNone(_resolve_sslmode(db, lambda k, default=None: None))

    def test_loopback_ip_keeps_ssl_disabled(self):
        db = {"HOST": "127.0.0.1", "SSLMODE": None}
        self.assertIsNone(_resolve_sslmode(db, lambda k, default=None: None))

    def test_render_com_host_defaults_to_require(self):
        db = {"HOST": "dpg-xxxx-a.singapore-postgres.render.com", "SSLMODE": None}
        self.assertEqual(_resolve_sslmode(db, lambda k, default=None: None), "require")

    def test_render_env_defaults_to_require_for_remote_internal_host(self):
        db = {"HOST": "dpg-xxxx-a", "SSLMODE": None}
        env_map = {"RENDER": "true"}
        self.assertEqual(_resolve_sslmode(db, lambda k, default=None: env_map.get(k, default)), "require")

    def test_database_url_sslmode_query_param_preserved(self):
        db = {"HOST": "db.custom.org", "SSLMODE": "prefer"}
        self.assertEqual(_resolve_sslmode(db, lambda k, default=None: None), "prefer")

    def test_postgres_sslmode_env_override(self):
        db = {"HOST": "dpg-xxxx-a.singapore-postgres.render.com", "SSLMODE": None}
        env_map = {"POSTGRES_SSLMODE": "disable"}
        self.assertEqual(_resolve_sslmode(db, lambda k, default=None: env_map.get(k, default)), "disable")


class PostgresFromUrlTests(SimpleTestCase):
    def test_parse_postgres_scheme(self):
        parsed = _postgres_from_url("postgres://app_user:secret_pw@db.render.internal:5432/lostfound_db")
        self.assertEqual(parsed["NAME"], "lostfound_db")
        self.assertEqual(parsed["USER"], "app_user")
        self.assertEqual(parsed["PASSWORD"], "secret_pw")
        self.assertEqual(parsed["HOST"], "db.render.internal")
        self.assertEqual(parsed["PORT"], "5432")
        self.assertIsNone(parsed["SSLMODE"])

    def test_parse_postgresql_scheme(self):
        parsed = _postgres_from_url("postgresql://app_user:secret_pw@dpg-abc-a.render.com:5432/lostfound_db")
        self.assertEqual(parsed["NAME"], "lostfound_db")
        self.assertEqual(parsed["USER"], "app_user")
        self.assertEqual(parsed["PASSWORD"], "secret_pw")
        self.assertEqual(parsed["HOST"], "dpg-abc-a.render.com")
        self.assertEqual(parsed["PORT"], "5432")

    def test_default_port_when_omitted(self):
        parsed = _postgres_from_url("postgres://lostfound:secret@dpg-abc-a/lostfound")
        self.assertEqual(parsed["PORT"], "5432")
        self.assertEqual(parsed["HOST"], "dpg-abc-a")
        self.assertEqual(parsed["NAME"], "lostfound")

    def test_sslmode_in_query_string(self):
        parsed = _postgres_from_url("postgres://lostfound:secret@dpg-abc-a.singapore-postgres.render.com/lostfound?sslmode=require")
        self.assertEqual(parsed["SSLMODE"], "require")
        self.assertEqual(parsed["HOST"], "dpg-abc-a.singapore-postgres.render.com")

    def test_url_encoded_credentials(self):
        parsed = _postgres_from_url("postgres://user%40campus.edu:p%40ss%23word@localhost:5432/lostfound")
        self.assertEqual(parsed["USER"], "user@campus.edu")
        self.assertEqual(parsed["PASSWORD"], "p@ss#word")

    def test_whitespace_handling(self):
        parsed = _postgres_from_url("   postgres://lostfound:secret@localhost:5432/lostfound   \n")
        self.assertEqual(parsed["NAME"], "lostfound")
        self.assertEqual(parsed["HOST"], "localhost")

    def test_reject_sqlite_and_other_schemes(self):
        for bad_url in ("sqlite:///db.sqlite3", "mysql://root:pw@localhost/db", "http://example.com"):
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(ImproperlyConfigured):
                    _postgres_from_url(bad_url)


class StartupChecksSecurityTests(SimpleTestCase):
    def test_describe_target_does_not_expose_password(self):
        with mock.patch("django.conf.settings.DATABASES", {
            "default": {
                "NAME": "test_db",
                "USER": "test_user",
                "PASSWORD": "super_secret_password_do_not_leak",
                "HOST": "remote.postgres.render.com",
                "PORT": "5432",
            }
        }):
            desc = checks.describe_target()
            self.assertIn("test_db", desc)
            self.assertIn("test_user", desc)
            self.assertIn("remote.postgres.render.com", desc)
            self.assertNotIn("super_secret_password_do_not_leak", desc)

    def test_postgres_available_masks_credentials_on_error(self):
        secret_pw = "super_secret_password_do_not_leak"
        err_message = f"FATAL: password authentication failed for user test_user (password: {secret_pw})"

        mock_conn = mock.MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.cursor.side_effect = DatabaseError(err_message)

        with mock.patch("django.conf.settings.DATABASES", {
            "default": {
                "NAME": "test_db",
                "USER": "test_user",
                "PASSWORD": secret_pw,
                "HOST": "remote.postgres.render.com",
                "PORT": "5432",
            }
        }), mock.patch("lostfound.checks.connection", mock_conn):
            errors = checks.postgres_available(None)
            self.assertEqual(len(errors), 1)
            err = errors[0]
            self.assertIsInstance(err, Error)
            self.assertEqual(err.id, "lostfound.E002")
            self.assertNotIn(secret_pw, err.msg)
            self.assertIn("******", err.msg)

    def test_postgres_available_masks_url_passwords(self):
        url_with_pw = "Cannot connect to postgres://user:exposed_pass123@remote.host:5432/db"

        mock_conn = mock.MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.cursor.side_effect = DatabaseError(url_with_pw)

        with mock.patch("django.conf.settings.DATABASES", {
            "default": {
                "NAME": "test_db",
                "USER": "user",
                "PASSWORD": "different_pw",
                "HOST": "remote.host",
                "PORT": "5432",
            }
        }), mock.patch("lostfound.checks.connection", mock_conn):
            errors = checks.postgres_available(None)
            self.assertEqual(len(errors), 1)
            self.assertNotIn("exposed_pass123", errors[0].msg)
            self.assertIn("postgres://user:******@remote.host:5432/db", errors[0].msg)

    def test_postgres_available_rejects_non_postgresql_vendor(self):
        mock_conn = mock.MagicMock()
        mock_conn.vendor = "sqlite"
        with mock.patch("lostfound.checks.connection", mock_conn):
            errors = checks.postgres_available(None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "lostfound.E001")
            self.assertIn("PostgreSQL only", errors[0].msg)
