"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/logging_config.py — Structured JSON Logging
==============================================================================

PURPOSE
-------
Production observability requires machine-readable logs. This module replaces
Python's default plaintext formatter with a JSON formatter that emits one
JSON object per line, fully compatible with:
  - Datadog Log Management
  - Grafana Loki
  - AWS CloudWatch Logs Insights
  - Elastic ELK Stack
  - Google Cloud Logging

JSON LOG SCHEMA (per line)
--------------------------
{
  "timestamp":  "2026-06-11T14:00:00.123456Z",   # ISO-8601 UTC
  "level":      "INFO",                            # Log level
  "logger":     "aegis.orchestrator",              # Logger name
  "message":    "Pipeline stage complete",         # Log message
  "run_id":     "a1b2c3d4",                        # Optional: current pipeline run
  "stage":      "WASM_SANDBOX",                    # Optional: pipeline stage
  "module":     "orchestrator",                    # Python module
  "line":       "247",                             # Source line number
  "exc_info":   "..."                              # Exception traceback if present
}

USAGE
-----
    from aegis_v3.logging_config import configure_logging
    configure_logging(level="INFO", json_format=True, log_file="aegis_v3.log")

==============================================================================
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from typing import Optional


def configure_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """
    Configure root and Aegis-specific loggers with structured JSON output.

    Args:
        level:       Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_format: If True, use JSON formatter; else use readable format.
        log_file:    Optional path to write logs to a rotating file.
        run_id:      Optional pipeline run ID to inject into every log record.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # -----------------------------------------------------------------------
    # Choose formatter
    # -----------------------------------------------------------------------
    if json_format:
        try:
            from pythonjsonlogger import jsonlogger  # type: ignore

            class _AegisJsonFormatter(jsonlogger.JsonFormatter):
                def add_fields(
                    self,
                    log_record: dict,
                    record: logging.LogRecord,
                    message_dict: dict,
                ) -> None:
                    super().add_fields(log_record, record, message_dict)
                    # Rename 'asctime' → 'timestamp' and force UTC ISO format
                    import datetime
                    log_record["timestamp"] = (
                        datetime.datetime.utcnow().isoformat() + "Z"
                    )
                    log_record.pop("asctime", None)
                    log_record["logger"] = record.name
                    log_record["level"]  = record.levelname
                    log_record["module"] = record.module
                    log_record["line"]   = record.lineno
                    if run_id:
                        log_record.setdefault("run_id", run_id)

            formatter: logging.Formatter = _AegisJsonFormatter(
                "%(timestamp)s %(level)s %(logger)s %(message)s"
            )
        except ImportError:
            # Graceful fallback if python-json-logger not yet installed
            formatter = _PlainFormatter(run_id=run_id)
    else:
        formatter = _PlainFormatter(run_id=run_id)

    # -----------------------------------------------------------------------
    # Root handler — stdout
    # -----------------------------------------------------------------------
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove any pre-existing handlers to avoid duplicate output
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(numeric_level)
    root_logger.addHandler(stdout_handler)

    # -----------------------------------------------------------------------
    # Optional rotating file handler
    # -----------------------------------------------------------------------
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(numeric_level)
        root_logger.addHandler(file_handler)
        logging.getLogger("aegis").info(
            f"Structured logging active | level={level} | file={log_file} | json={json_format}"
        )
    else:
        logging.getLogger("aegis").info(
            f"Structured logging active | level={level} | stdout only | json={json_format}"
        )

    # -----------------------------------------------------------------------
    # Silence noisy third-party loggers
    # -----------------------------------------------------------------------
    for noisy in ("uvicorn.access", "httpx", "chromadb", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _PlainFormatter(logging.Formatter):
    """Fallback plain-text formatter with run_id injection."""

    def __init__(self, run_id: Optional[str] = None) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        if self._run_id:
            record.msg = f"[{self._run_id}] {record.msg}"
        return super().format(record)
