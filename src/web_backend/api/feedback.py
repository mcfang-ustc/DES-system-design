"""
Feedback API endpoints

Handles experimental feedback submission for recommendations.
"""

import logging
from fastapi import APIRouter, HTTPException, status

from models.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    FeedbackAsyncResponse,
    FeedbackStatusResponse,
    FeedbackStatusData,
    FeedbackData,
    ErrorResponse
)
from services.feedback_service import get_feedback_service
from utils.response import error_response
from utils.exceptions import ValidationException

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit experimental feedback",
    description="Submit real laboratory experiment results for a recommendation (async processing)",
    responses={
        202: {"description": "Feedback accepted and processing started", "model": FeedbackAsyncResponse},
        400: {"description": "Validation error", "model": ErrorResponse},
        404: {"description": "Recommendation not found", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse}
    }
)
async def submit_feedback(feedback_request: FeedbackRequest):
    """
    Submit experimental feedback for a recommendation.

    This endpoint starts async feedback processing:
    1. User provides real laboratory measurements
    2. System validates the experimental data
    3. System updates recommendation status to PROCESSING
    4. System processes feedback in background thread
    5. User can poll /feedback/{rec_id}/status to check progress

    **Required fields**:
    - recommendation_id: ID of the recommendation to update
    - experiment_result.is_liquid_formed: Whether DES formed liquid phase
    - experiment_result.measurements: Long-table rows (required when is_liquid_formed=True; optional when False)

    **Validation rules (enforced in service)**:
    - If is_liquid_formed=True: at least one measurement row AND at least one row with leaching_efficiency
    - If is_liquid_formed=False: measurements may be empty; if provided, leaching_efficiency must be absent/0
    - For each provided measurement row: time_h >= 0, leaching_efficiency >= 0, unit required

    **Example request**:
    ```json
    {
      "recommendation_id": "REC_20251016_123456_task_001",
      "experiment_result": {
        "is_liquid_formed": true,
        "conditions": {
          "temperature_C": 25,
          "solid_liquid_ratio": { "solid_mass_g": 1.0, "liquid_volume_ml": 10.0 }
        },
        "measurements": [
          {"target_material": "Fe", "time_h": 1, "leaching_efficiency": 20, "unit": "%"},
          {"target_material": "Fe", "time_h": 3, "leaching_efficiency": 35, "unit": "%"},
          {"target_material": "Co", "time_h": 3, "leaching_efficiency": 5, "unit": "%"}
        ],
        "properties": {
          "viscosity": "45 cP",
          "density": "1.15 g/mL",
          "appearance": "clear liquid"
        },
        "experimenter": "Dr. Zhang",
        "notes": "DES formed successfully at room temperature. Clear homogeneous liquid observed."
      }
    }
    ```

    **Returns (immediate)**:
    - status: "accepted"
    - processing: "started"
    - recommendation_id: ID for status polling

    **Polling status**:
    Use GET /feedback/{rec_id}/status to check processing status
    """
    try:
        # Call feedback service (async mode)
        feedback_service = get_feedback_service()
        result = feedback_service.submit_feedback(
            feedback_request.recommendation_id,
            feedback_request.experiment_result,
            async_processing=True  # Enable async processing
        )

        # Return async response
        return FeedbackAsyncResponse(
            status="accepted",
            recommendation_id=result["recommendation_id"],
            processing=result["processing"],
            message=result.get("message", "Feedback accepted and processing in background")
        )

    except ValidationException as e:
        # Validation error or not found
        error_msg = e.message
        if "not found" in error_msg.lower():
            logger.warning(f"Recommendation not found: {feedback_request.recommendation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response(message=error_msg)
            )
        else:
            logger.warning(f"Validation error: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=error_response(
                    message=error_msg,
                    field=getattr(e, "field", None),
                    index=getattr(e, "index", None)
                )
            )

    except RuntimeError as e:
        logger.error(f"Failed to process feedback: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(message=str(e))
        )

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(message=f"Unexpected error: {str(e)}")
        )


@router.get(
    "/{recommendation_id}/status",
    response_model=FeedbackStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check feedback processing status",
    description="Check the status of async feedback processing",
    responses={
        200: {"description": "Status retrieved successfully", "model": FeedbackStatusResponse},
        404: {"description": "Status not found", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse}
    }
)
async def get_feedback_status(recommendation_id: str):
    """
    Check the processing status of a feedback submission.

    **Returns**:
    - status: "processing" | "completed" | "failed"
    - started_at: Processing start time
    - completed_at: Completion time (if completed)
    - result: Processing result (if completed)
    - error: Error message (if failed)

    **Example response (processing)**:
    ```json
    {
      "status": "success",
      "data": {
        "status": "processing",
        "started_at": "2025-10-20T14:30:00"
      }
    }
    ```

    **Example response (completed)**:
    ```json
    {
      "status": "success",
      "data": {
        "status": "completed",
        "started_at": "2025-10-20T14:30:00",
        "completed_at": "2025-10-20T14:30:45",
        "result": {
          "recommendation_id": "REC_...",
          "is_liquid_formed": true,
          "measurement_count": 10,
          "memories_extracted": ["Memory 1", "Memory 2"],
          "num_memories": 2,
          "is_update": false,
          "deleted_memories": 0
        }
      }
    }
    ```
    """
    try:
        feedback_service = get_feedback_service()
        status_data = feedback_service.check_processing_status(recommendation_id)

        if not status_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response(message=f"No processing status found for {recommendation_id}")
            )

        # Build FeedbackData if result exists
        result = None
        if status_data.get("result"):
            result_data = status_data["result"]
            result = FeedbackData(
                recommendation_id=recommendation_id,
                is_liquid_formed=result_data.get("is_liquid_formed"),
                measurement_count=result_data.get("measurement_count", 0),
                memories_extracted=result_data.get("memories_extracted", []),
                num_memories=result_data.get("num_memories", 0),
                experiment_summary_text=result_data.get("experiment_summary_text")
            )

        return FeedbackStatusResponse(
            status="success",
            data=FeedbackStatusData(
                status=status_data["status"],
                started_at=status_data["started_at"],
                completed_at=status_data.get("completed_at"),
                failed_at=status_data.get("failed_at"),
                result=result,
                error=status_data.get("error"),
                is_update=status_data.get("result", {}).get("is_update") if status_data.get("result") else None,
                deleted_memories=status_data.get("result", {}).get("deleted_memories") if status_data.get("result") else None
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get feedback status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(message=f"Failed to get feedback status: {str(e)}")
        )
