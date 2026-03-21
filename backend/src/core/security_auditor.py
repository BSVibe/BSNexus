"""Security auditor for vulnerability scanning and configuration assessment."""

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.src.config import Settings


class SeverityLevel(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class FindingCategory(str, enum.Enum):
    configuration = "configuration"
    authentication = "authentication"
    encryption = "encryption"
    cors = "cors"
    rate_limiting = "rate_limiting"
    headers = "headers"
    dependency = "dependency"
    input_validation = "input_validation"


@dataclass
class SecurityFinding:
    """A single security finding from an audit scan."""

    category: FindingCategory
    severity: SeverityLevel
    title: str
    description: str
    recommendation: str
    affected_component: str | None = None


@dataclass
class SecurityReport:
    """Complete security audit report."""

    scan_timestamp: datetime
    findings: list[SecurityFinding] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def add_finding(self, finding: SecurityFinding) -> None:
        self.findings.append(finding)

    def build_summary(self) -> dict[str, int]:
        """Count findings by severity."""
        counts: dict[str, int] = {level.value: 0 for level in SeverityLevel}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        self.summary = counts
        return counts

    @property
    def has_critical(self) -> bool:
        return any(f.severity == SeverityLevel.critical for f in self.findings)

    @property
    def passed(self) -> bool:
        """Report passes if no critical or high severity findings."""
        return not any(f.severity in (SeverityLevel.critical, SeverityLevel.high) for f in self.findings)

    def to_dict(self) -> dict:
        self.build_summary()
        return {
            "scan_timestamp": self.scan_timestamp.isoformat(),
            "passed": self.passed,
            "summary": self.summary,
            "findings": [
                {
                    "category": f.category.value,
                    "severity": f.severity.value,
                    "title": f.title,
                    "description": f.description,
                    "recommendation": f.recommendation,
                    "affected_component": f.affected_component,
                }
                for f in self.findings
            ],
        }


class SecurityAuditor:
    """Performs security vulnerability scans against the application configuration.

    NOTE: This is a static configuration checker — it validates settings values
    against known-good patterns. It does not perform dynamic penetration testing
    or runtime vulnerability scanning.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run_full_scan(self) -> SecurityReport:
        """Run all security checks and return a comprehensive report."""
        report = SecurityReport(scan_timestamp=datetime.now(timezone.utc))

        self._check_signing_key(report)
        self._check_encryption_key(report)
        self._check_debug_mode(report)
        self._check_database_url(report)
        self._check_cors_config(report)
        self._check_rate_limiting(report)

        report.build_summary()
        return report

    def _check_signing_key(self, report: SecurityReport) -> None:
        """Check prompt signing key configuration."""
        key = self._settings.prompt_signing_key
        if key == "dev-signing-key-change-in-production":
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.encryption,
                    severity=SeverityLevel.critical,
                    title="Default prompt signing key in use",
                    description="The prompt signing key is set to the default development value.",
                    recommendation='Generate a secure key with: python -c "import secrets; print(secrets.token_hex(32))"',
                    affected_component="config.prompt_signing_key",
                )
            )
        elif len(key) < 32:
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.encryption,
                    severity=SeverityLevel.high,
                    title="Weak prompt signing key",
                    description=f"Signing key is only {len(key)} characters. Minimum 32 recommended.",
                    recommendation="Use a key of at least 32 characters generated with secrets.token_hex(32).",
                    affected_component="config.prompt_signing_key",
                )
            )

    def _check_encryption_key(self, report: SecurityReport) -> None:
        """Check encryption key configuration."""
        key = getattr(self._settings, "encryption_key", None)
        if not key or key == "dev-encryption-key-change-in-production":
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.encryption,
                    severity=SeverityLevel.critical,
                    title="Default or missing encryption key",
                    description="The data encryption key is not configured or uses the default value.",
                    recommendation="Set ENCRYPTION_KEY environment variable with a secure random key.",
                    affected_component="config.encryption_key",
                )
            )

    def _check_debug_mode(self, report: SecurityReport) -> None:
        """Check if debug mode is enabled."""
        if self._settings.debug:
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.configuration,
                    severity=SeverityLevel.high,
                    title="Debug mode enabled",
                    description="Application is running in debug mode, which may expose sensitive information.",
                    recommendation="Set DEBUG=false in production.",
                    affected_component="config.debug",
                )
            )

    def _check_database_url(self, report: SecurityReport) -> None:
        """Check database URL for security issues."""
        db_url = self._settings.database_url
        if "bsnexus_dev" in db_url or "password" in db_url.lower():
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.configuration,
                    severity=SeverityLevel.high,
                    title="Default database credentials detected",
                    description="Database URL appears to use default or weak credentials.",
                    recommendation="Use strong, unique database credentials in production.",
                    affected_component="config.database_url",
                )
            )

    def _check_cors_config(self, report: SecurityReport) -> None:
        """Check CORS configuration."""
        allowed_origins = getattr(self._settings, "cors_allowed_origins", ["*"])
        if "*" in allowed_origins:
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.cors,
                    severity=SeverityLevel.high,
                    title="CORS allows all origins",
                    description="CORS is configured to accept requests from any origin.",
                    recommendation="Restrict CORS to specific trusted origins in production.",
                    affected_component="config.cors_allowed_origins",
                )
            )

    def _check_rate_limiting(self, report: SecurityReport) -> None:
        """Check rate limiting configuration."""
        enabled = getattr(self._settings, "rate_limit_enabled", True)
        if not enabled:
            report.add_finding(
                SecurityFinding(
                    category=FindingCategory.rate_limiting,
                    severity=SeverityLevel.medium,
                    title="Rate limiting disabled",
                    description="API rate limiting is not enabled.",
                    recommendation="Enable rate limiting to protect against abuse and DDoS attacks.",
                    affected_component="config.rate_limit_enabled",
                )
            )
