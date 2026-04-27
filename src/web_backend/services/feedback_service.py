"""
Feedback Service

Business logic for submitting and processing experimental feedback.
Supports both synchronous and asynchronous (background) processing.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading

from models.schemas import (
    ExperimentResultRequest,
    FeedbackData,
    DissolutionMeasurement,
    ExperimentConditions,
)
from utils.exceptions import ValidationException
from utils.agent_loader import get_agent, get_rec_manager

logger = logging.getLogger(__name__)


def _to_plain(obj: Any) -> Any:
    """
    Convert Pydantic models / dataclasses / other objects into plain
    Python types (dict / list / primitives) so they are JSON-serializable.

    This is mainly used to ensure that the agent-side ExperimentResult
    dataclass only contains primitive types before we call asdict()/json.dump().
    """
    if obj is None:
        return None

    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass

    # Pydantic v1 fallback
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass

    if isinstance(obj, dict):
        return obj

    # Best-effort mapping conversion (for dataclasses / custom types)
    try:
        return dict(obj)  # type: ignore[arg-type]
    except Exception:
        return obj


class FeedbackService:
    """Service for managing experimental feedback"""

    def __init__(self, max_workers: int = 4):
        """
        Initialize feedback service with background processing support.

        Args:
            max_workers: Maximum number of concurrent background tasks
        """
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="feedback")
        self.processing_status = {}  # {rec_id: {"status": "processing|completed|failed", "result": ...}}
        self.status_lock = threading.Lock()
        logger.info(f"Initialized FeedbackService with {max_workers} worker threads")

    def submit_feedback(
        self,
        recommendation_id: str,
        experiment_result: ExperimentResultRequest,
        async_processing: bool = True
    ) -> Dict[str, Any]:
        """
        Submit experimental feedback for a recommendation.

        Args:
            recommendation_id: ID of the recommendation
            experiment_result: Experimental result data
            async_processing: If True, process in background and return immediately
                             If False, process synchronously (blocks until done)

        Returns:
            If async_processing=True: {"status": "accepted", "processing": "started"}
            If async_processing=False: FeedbackData with complete results

        Raises:
            ValueError: If validation fails or recommendation not found
            RuntimeError: If feedback processing fails (sync mode only)
        """
        logger.info(f"Submitting feedback for recommendation: {recommendation_id}")

        try:
            # Validate recommendation exists and is in valid state
            rec_manager = get_rec_manager()
            rec = rec_manager.get_recommendation(recommendation_id)

            if not rec:
                raise ValidationException(
                    f"Recommendation {recommendation_id} not found",
                    field="recommendation_id"
                )

            if rec.status == "CANCELLED":
                raise ValidationException(
                    f"Cannot submit feedback for cancelled recommendation {recommendation_id}",
                    field="recommendation_id"
                )

            if rec.status == "COMPLETED":
                logger.warning(
                    f"Recommendation {recommendation_id} already has feedback. "
                    "This will update the existing feedback."
                )

            # Validate experiment result
            self._validate_experiment_result(experiment_result)

            # Convert to agent's ExperimentResult format
            from agent.reasoningbank import ExperimentResult

            agent_exp_result = ExperimentResult(
                is_liquid_formed=experiment_result.is_liquid_formed,
                # Ensure everything passed into the dataclass is plain Python data
                properties=_to_plain(experiment_result.properties) or {},
                conditions=_to_plain(experiment_result.conditions) or {},
                measurements=[_to_plain(m) for m in experiment_result.measurements],
                experimenter=experiment_result.experimenter,
                experiment_date=datetime.now().isoformat(),
                notes=experiment_result.notes
            )

            logger.info(
                f"Experiment result: liquid_formed={agent_exp_result.is_liquid_formed}, "
                f"measurements={len(agent_exp_result.measurements)}"
            )

            # Decide: async or sync processing
            if async_processing:
                return self._submit_feedback_async(recommendation_id, agent_exp_result)
            else:
                return self._submit_feedback_sync(recommendation_id, agent_exp_result)

        except ValidationException as e:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Failed to submit feedback: {e}", exc_info=True)
            raise RuntimeError(f"Failed to submit feedback: {str(e)}")

    def _submit_feedback_sync(self, recommendation_id: str, agent_exp_result) -> FeedbackData:
        """Synchronous feedback processing (blocks until complete)"""
        agent = get_agent()
        result = agent.submit_experiment_feedback(recommendation_id, agent_exp_result)

        # Check if processing succeeded
        if result["status"] != "success":
            raise RuntimeError(f"Feedback processing failed: {result.get('message')}")

        logger.info(f"Feedback processed: memories={len(result['memories_extracted'])}")

        # Build response data
        return FeedbackData(
            recommendation_id=recommendation_id,
            is_liquid_formed=result.get("is_liquid_formed"),
            measurement_count=result.get("measurement_count", 0),
            memories_extracted=result["memories_extracted"],
            num_memories=len(result["memories_extracted"]),
            experiment_summary_text=result.get("experiment_summary_text")
        )

    def _submit_feedback_async(self, recommendation_id: str, agent_exp_result) -> Dict[str, Any]:
        """Asynchronous feedback processing (returns immediately)"""
        # Update recommendation status to PROCESSING
        rec_manager = get_rec_manager()
        rec_manager.update_status(recommendation_id, "PROCESSING")

        # Initialize processing status
        with self.status_lock:
            self.processing_status[recommendation_id] = {
                "status": "processing",
                "started_at": datetime.now().isoformat(),
                "result": None,
                "error": None
            }

        # Submit to thread pool
        future = self.executor.submit(
            self._background_process_feedback,
            recommendation_id,
            agent_exp_result
        )

        logger.info(f"Submitted feedback processing for {recommendation_id} to background thread")

        return {
            "status": "accepted",
            "recommendation_id": recommendation_id,
            "processing": "started",
            "message": "Feedback accepted and processing in background"
        }

    def _background_process_feedback(self, recommendation_id: str, agent_exp_result):
        """Background task to process feedback (runs in thread pool)"""
        try:
            logger.info(f"[Background] Processing feedback for {recommendation_id}")

            # Call agent to process feedback
            agent = get_agent()
            result = agent.submit_experiment_feedback(recommendation_id, agent_exp_result)

            # Check if processing succeeded
            if result["status"] != "success":
                raise RuntimeError(f"Feedback processing failed: {result.get('message')}")

            # Update status to completed
            with self.status_lock:
                self.processing_status[recommendation_id] = {
                    "status": "completed",
                    "started_at": self.processing_status[recommendation_id]["started_at"],
                    "completed_at": datetime.now().isoformat(),
                    "result": {
                        "is_liquid_formed": result.get("is_liquid_formed"),
                    "measurement_count": result.get("measurement_count", 0),
                    "memories_extracted": result["memories_extracted"],
                    "num_memories": len(result["memories_extracted"]),
                    "experiment_summary_text": result.get("experiment_summary_text"),
                    "is_update": result.get("is_update", False),
                    "deleted_memories": result.get("deleted_memories", 0)
                },
                    "error": None
                }

            logger.info(f"[Background] Completed feedback processing for {recommendation_id}")

        except Exception as e:
            logger.error(f"[Background] Failed to process feedback for {recommendation_id}: {e}", exc_info=True)

            # Update recommendation status to FAILED
            rec_manager = get_rec_manager()
            rec_manager.update_status(recommendation_id, "FAILED")

            # Update processing status
            with self.status_lock:
                self.processing_status[recommendation_id] = {
                    "status": "failed",
                    "started_at": self.processing_status[recommendation_id]["started_at"],
                    "failed_at": datetime.now().isoformat(),
                    "result": None,
                    "error": str(e)
                }

    def check_processing_status(self, recommendation_id: str) -> Optional[Dict[str, Any]]:
        """
        Check the processing status of a feedback submission.

        Args:
            recommendation_id: Recommendation ID to check

        Returns:
            Dict with status info, or None if not found

        Example:
            {
                "status": "processing|completed|failed",
                "started_at": "2025-10-20T14:30:00",
                "completed_at": "2025-10-20T14:30:45",  # if completed
                "result": {...},  # if completed
                "error": "..."    # if failed
            }
        """
        with self.status_lock:
            return self.processing_status.get(recommendation_id)

    def _validate_experiment_result(self, exp_result: ExperimentResultRequest) -> None:
        """
        Validate experiment result data.

        Args:
            exp_result: Experiment result to validate

        Raises:
            ValueError: If validation fails
        """
        measurements = exp_result.measurements or []

        # Require measurements only when liquid formed.
        # If DES did not form, users may have no valid leaching time-series to report.
        if exp_result.is_liquid_formed and len(measurements) == 0:
            raise ValidationException(
                "At least one measurement is required when is_liquid_formed=True",
                field="measurements",
            )

        # If liquid formed: require at least one measurement with leaching_efficiency
        if exp_result.is_liquid_formed:
            has_eff = any((m.leaching_efficiency is not None) for m in measurements)
            if not has_eff:
                raise ValidationException(
                    "When is_liquid_formed=True, provide at least one measurement.leaching_efficiency",
                    field="measurements"
                )
        else:
            # If not formed, leaching_efficiency should be absent
            bad = [
                (m.target_material, m.time_h)
                for m in measurements
                if m.leaching_efficiency not in (None, 0)
            ]
            if bad:
                raise ValidationException(
                    "is_liquid_formed=False but some measurements include leaching_efficiency > 0",
                    field="measurements"
                )

        # Basic measurements sanity checks (no silent fixes)
        seen: set = set()
        for idx, m in enumerate(measurements):
            if m.time_h is None or m.time_h < 0:
                raise ValidationException(
                    "time_h must be provided and non-negative",
                    field="time_h",
                    index=idx
                )
            if m.leaching_efficiency is not None and m.leaching_efficiency < 0:
                raise ValidationException(
                    "leaching_efficiency cannot be negative",
                    field="leaching_efficiency",
                    index=idx
                )
            if not m.unit:
                raise ValidationException(
                    "unit is required for each measurement",
                    field="unit",
                    index=idx
                )
            key = (m.target_material.strip().lower(), m.time_h)
            if key in seen:
                raise ValidationException(
                    f"Duplicate measurement for target_material={m.target_material}, time_h={m.time_h}",
                    field="measurements",
                    index=idx
                )
            seen.add(key)


# Singleton instance
_service: FeedbackService = None


def get_feedback_service() -> FeedbackService:
    """Get feedback service singleton"""
    global _service
    if _service is None:
        _service = FeedbackService()
    return _service
