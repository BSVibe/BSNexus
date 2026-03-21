"""Tests for SecurityAuditor."""

import pytest

from backend.src.config import Settings
from backend.src.core.security_auditor import (
    FindingCategory,
    SecurityAuditor,
    SecurityReport,
    SeverityLevel,
)


class TestSecurityAuditor:
    def test_detects_default_signing_key(self):
        s = Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            prompt_signing_key="dev-signing-key-change-in-production",
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        signing_findings = [f for f in report.findings if "signing key" in f.title.lower()]
        assert len(signing_findings) >= 1
        assert signing_findings[0].severity == SeverityLevel.critical

    def test_detects_weak_signing_key(self):
        s = Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            prompt_signing_key="short",
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        signing_findings = [f for f in report.findings if "signing key" in f.title.lower()]
        assert any(f.severity == SeverityLevel.high for f in signing_findings)

    def test_detects_debug_mode(self):
        s = Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            prompt_signing_key="a-very-long-signing-key-that-is-secure-enough",
            debug=True,
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        debug_findings = [f for f in report.findings if "debug" in f.title.lower()]
        assert len(debug_findings) >= 1

    def test_detects_default_db_credentials(self):
        s = Settings(
            database_url="postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus",
            prompt_signing_key="a-very-long-signing-key-that-is-secure-enough",
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        db_findings = [f for f in report.findings if "database" in f.title.lower()]
        assert len(db_findings) >= 1

    def test_detects_wildcard_cors(self):
        s = Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            prompt_signing_key="a-very-long-signing-key-that-is-secure-enough",
            cors_allowed_origins=["*"],
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        cors_findings = [f for f in report.findings if "cors" in f.title.lower()]
        assert len(cors_findings) >= 1

    def test_detects_rate_limiting_disabled(self):
        s = Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            prompt_signing_key="a-very-long-signing-key-that-is-secure-enough",
            rate_limit_enabled=False,
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        rl_findings = [f for f in report.findings if "rate" in f.title.lower()]
        assert len(rl_findings) >= 1

    def test_secure_config_passes(self):
        s = Settings(
            database_url="postgresql+asyncpg://produser:strongpass@prodhost/proddb",
            prompt_signing_key="a-very-long-and-secure-signing-key-for-production-use",
            encryption_key="another-very-long-and-secure-encryption-key-here",
            debug=False,
            cors_allowed_origins=["https://app.example.com"],
            rate_limit_enabled=True,
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        assert report.passed is True
        assert report.has_critical is False


class TestSecurityReport:
    def test_build_summary(self):
        report = SecurityReport(scan_timestamp=pytest.importorskip("datetime").datetime.now())
        from backend.src.core.security_auditor import SecurityFinding

        report.add_finding(SecurityFinding(
            category=FindingCategory.configuration,
            severity=SeverityLevel.critical,
            title="Test",
            description="Test",
            recommendation="Fix it",
        ))
        report.add_finding(SecurityFinding(
            category=FindingCategory.configuration,
            severity=SeverityLevel.low,
            title="Test 2",
            description="Test 2",
            recommendation="Fix it",
        ))

        summary = report.build_summary()
        assert summary["critical"] == 1
        assert summary["low"] == 1
        assert summary["high"] == 0

    def test_has_critical(self):
        from backend.src.core.security_auditor import SecurityFinding

        report = SecurityReport(scan_timestamp=pytest.importorskip("datetime").datetime.now())
        assert report.has_critical is False

        report.add_finding(SecurityFinding(
            category=FindingCategory.configuration,
            severity=SeverityLevel.critical,
            title="Test",
            description="Test",
            recommendation="Fix",
        ))
        assert report.has_critical is True

    def test_to_dict(self):
        from backend.src.core.security_auditor import SecurityFinding

        report = SecurityReport(scan_timestamp=pytest.importorskip("datetime").datetime.now())
        report.add_finding(SecurityFinding(
            category=FindingCategory.authentication,
            severity=SeverityLevel.medium,
            title="Auth issue",
            description="Desc",
            recommendation="Fix",
        ))
        d = report.to_dict()
        assert "scan_timestamp" in d
        assert "findings" in d
        assert "summary" in d
        assert d["summary"]["medium"] == 1

    def test_detects_default_encryption_key(self):
        """SecurityAuditor detects the default encryption key as a critical finding."""
        s = Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            encryption_key="dev-encryption-key-change-in-production",
            prompt_signing_key="a-very-long-signing-key-that-is-secure-enough",
        )
        auditor = SecurityAuditor(s)
        report = auditor.run_full_scan()

        enc_findings = [f for f in report.findings if "encryption key" in f.title.lower()]
        assert len(enc_findings) >= 1
        assert enc_findings[0].severity == SeverityLevel.critical
