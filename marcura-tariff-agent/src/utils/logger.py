"""
logger.py - Structured logging for HarbourMind calculations

Logs to both:
- Python logging (for Cloud Logging)
- JSON structure (for Cloud Storage)
"""

import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Optional


class CalculationLogger:
    """
    Structured logging for tariff calculations.

    Tracks:
    - Request start/end
    - Extraction metadata
    - Calculation results
    - Performance metrics
    """

    def __init__(self, calculation_id: str, logger: Optional[logging.Logger] = None):
        """
        Initialize calculation logger.

        Args:
            calculation_id: Unique identifier for this calculation
            logger: Python logger instance (optional)
        """
        self.calculation_id = calculation_id
        self.logger = logger or logging.getLogger(__name__)
        self.start_time = datetime.utcnow()
        self.events: List[Dict[str, Any]] = []

        # Initialize log structure
        self.log_data = {
            'calculation_id': calculation_id,
            'start_timestamp': self.start_time.isoformat(),
            'events': self.events
        }

    def log_request_start(
        self,
        vessel_name: str,
        port: str,
        tariff_file_size: Optional[int] = None,
        vessel_file_size: Optional[int] = None
    ) -> None:
        """
        Log the start of a calculation request.

        Args:
            vessel_name: Name of vessel
            port: Port name
            tariff_file_size: Size of tariff PDF in bytes
            vessel_file_size: Size of vessel PDF in bytes
        """
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'request_start',
            'vessel_name': vessel_name,
            'port': port,
            'tariff_file_size_bytes': tariff_file_size,
            'vessel_file_size_bytes': vessel_file_size
        }

        self.events.append(event)
        self.logger.info(f"Calculation started: {vessel_name} @ {port}")
        self.log_data.update({
            'vessel_name': vessel_name,
            'port': port
        })

    def log_extraction_start(self) -> None:
        """Log the start of rule/vessel extraction."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'extraction_start'
        }
        self.events.append(event)
        self.logger.info("Starting document extraction...")

    def log_extraction_complete(
        self,
        charges_found: int,
        confidence: float,
        duration_ms: int
    ) -> None:
        """
        Log completion of extraction phase.

        Args:
            charges_found: Number of charge types extracted
            confidence: Average confidence score (0-1)
            duration_ms: Extraction duration in milliseconds
        """
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'extraction_complete',
            'charges_found': charges_found,
            'average_confidence': confidence,
            'duration_ms': duration_ms
        }

        self.events.append(event)
        self.logger.info(
            f"Extraction complete: {charges_found} charges, "
            f"confidence: {confidence:.2%}, duration: {duration_ms}ms"
        )

        self.log_data.update({
            'extraction': {
                'charges_discovered': charges_found,
                'confidence': confidence,
                'time_ms': duration_ms
            }
        })

    def log_validation_start(self) -> None:
        """Log the start of validation phase."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'validation_start'
        }
        self.events.append(event)
        self.logger.info("Starting validation...")

    def log_validation_complete(self, is_valid: bool, issues: Optional[List[str]] = None) -> None:
        """
        Log completion of validation phase.

        Args:
            is_valid: Whether validation passed
            issues: List of validation issues (if any)
        """
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'validation_complete',
            'is_valid': is_valid,
            'issues': issues or []
        }

        self.events.append(event)

        if is_valid:
            self.logger.info("Validation passed")
        else:
            self.logger.warning(f"Validation failed: {', '.join(issues or [])}")

    def log_calculation_start(self) -> None:
        """Log the start of the calculation phase."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'calculation_start'
        }
        self.events.append(event)
        self.logger.info("Starting tariff calculation...")

    def log_calculation_complete(
        self,
        charges: List[Dict[str, Any]],
        subtotal: float,
        vat_amount: float,
        grand_total: float,
        duration_ms: int
    ) -> None:
        """
        Log completion of calculation phase.

        Args:
            charges: List of calculated charges with amounts
            subtotal: Subtotal before VAT
            vat_amount: VAT amount
            grand_total: Grand total
            duration_ms: Calculation duration in milliseconds
        """
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'calculation_complete',
            'charges_calculated': len(charges),
            'subtotal': subtotal,
            'vat_amount': vat_amount,
            'grand_total': grand_total,
            'duration_ms': duration_ms
        }

        self.events.append(event)
        self.logger.info(
            f"Calculation complete: R{grand_total:.2f} total, "
            f"duration: {duration_ms}ms"
        )

        self.log_data.update({
            'charges': charges,
            'subtotal': float(subtotal),
            'vat_amount': float(vat_amount),
            'grand_total': float(grand_total)
        })

    def log_request_complete(
        self,
        status: str = "success",
        error: Optional[str] = None
    ) -> None:
        """
        Log the completion of the entire request.

        Args:
            status: Status of the request (success/error)
            error: Error message if failed
        """
        end_time = datetime.utcnow()
        total_duration = int((end_time - self.start_time).total_seconds() * 1000)

        event = {
            'timestamp': end_time.isoformat(),
            'event_type': 'request_complete',
            'status': status,
            'total_duration_ms': total_duration,
            'error': error
        }

        self.events.append(event)

        if status == "success":
            self.logger.info(f"Calculation successful: {total_duration}ms total")
        else:
            self.logger.error(f"Calculation failed: {error}")

        self.log_data.update({
            'end_timestamp': end_time.isoformat(),
            'processing_time_ms': total_duration,
            'status': status
        })

    def get_log_data(self) -> Dict[str, Any]:
        """
        Get the complete log data structure.

        Returns:
            Dictionary containing all logged information
        """
        return self.log_data

    def get_json(self) -> str:
        """
        Get the log data as JSON string.

        Returns:
            JSON representation of log data
        """
        return json.dumps(self.log_data, indent=2, default=str)

    def log_error(self, error_type: str, message: str, details: Optional[Dict] = None) -> None:
        """
        Log an error that occurred during processing.

        Args:
            error_type: Type of error (e.g., PDF_PARSE_ERROR, CALC_ERROR)
            message: Error message
            details: Additional error details
        """
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'error',
            'error_type': error_type,
            'message': message,
            'details': details or {}
        }

        self.events.append(event)
        self.logger.error(f"{error_type}: {message}", extra=details or {})
