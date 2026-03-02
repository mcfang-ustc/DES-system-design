"""
Recommendation Service

Business logic for managing DES formulation recommendations.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from math import ceil

from models.schemas import (
    RecommendationSummary,
    RecommendationListData,
    RecommendationDetail,
    FormulationData,
    ComponentData,
    Trajectory,
    TrajectoryStep,
    ExperimentResultData,
    MemoryItemSummary
)
from utils.agent_loader import get_rec_manager, get_agent

logger = logging.getLogger(__name__)


class RecommendationService:
    """Service for managing recommendations"""

    def __init__(self):
        """Initialize recommendation service"""
        pass

    def list_recommendations(
        self,
        status: Optional[str] = None,
        material: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> RecommendationListData:
        """
        List recommendations with filtering and pagination (fast - index only).

        Args:
            status: Filter by status (PENDING, COMPLETED, CANCELLED)
            material: Filter by target_material
            page: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            RecommendationListData with items and pagination
        """
        logger.info(f"Listing recommendations: status={status}, material={material}, page={page}")

        try:
            # Get recommendation manager
            rec_manager = get_rec_manager()

            # Use fast list method (index only, no file I/O)
            result = rec_manager.list_recommendations_fast(
                status=status,
                target_material=material,
                page=page,
                page_size=page_size
            )

            # Convert index metadata to RecommendationSummary objects
            items = []
            for meta in result["items"]:
                # Parse formulation from index
                formulation_dict = meta.get("formulation", {})

                # Check if multi-component or binary
                if "components" in formulation_dict and formulation_dict.get("components"):
                    # Multi-component
                    components = [
                        ComponentData(
                            name=comp.get("name", "Unknown"),
                            role=comp.get("role", "Unknown"),
                            function=comp.get("function")
                        )
                        for comp in formulation_dict["components"]
                    ]
                    formulation = FormulationData(
                        components=components,
                        num_components=formulation_dict.get("num_components", len(components)),
                        molar_ratio=formulation_dict.get("molar_ratio", "Unknown")
                    )
                else:
                    # Binary formulation
                    formulation = FormulationData(
                        HBD=formulation_dict.get("HBD", "Unknown"),
                        HBA=formulation_dict.get("HBA", "Unknown"),
                        molar_ratio=formulation_dict.get("molar_ratio", "Unknown")
                    )

                summary = RecommendationSummary(
                    recommendation_id=meta["recommendation_id"],
                    task_id=meta["task_id"],
                    target_material=meta.get("target_material", "unknown"),
                    target_temperature=meta.get("target_temperature", 25.0),
                    formulation=formulation,
                    confidence=meta.get("confidence", 0.0),
                    status=meta["status"],
                    created_at=meta["created_at"],
                    updated_at=meta["updated_at"],
                    performance_score=meta.get("performance_score")
                )
                items.append(summary)

            return RecommendationListData(
                items=items,
                pagination=result["pagination"]
            )

        except Exception as e:
            logger.error(f"Failed to list recommendations: {e}", exc_info=True)
            raise RuntimeError(f"Failed to list recommendations: {str(e)}")

    def list_recommendations_old(
        self,
        status: Optional[str] = None,
        material: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> RecommendationListData:
        """
        [DEPRECATED] List recommendations (old method - loads full files).

        Kept for reference. Use list_recommendations() instead.
        """
        logger.info(f"Listing recommendations (OLD): status={status}, material={material}, page={page}")

        try:
            # Get recommendation manager
            rec_manager = get_rec_manager()

            # Get all recommendations with filters (no pagination in manager yet)
            all_recs = rec_manager.list_recommendations(
                status=status,
                target_material=material,
                limit=10000  # Get all for manual pagination
            )

            # Calculate pagination
            total = len(all_recs)
            total_pages = ceil(total / page_size) if total > 0 else 1
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size

            # Get page items
            page_recs = all_recs[start_idx:end_idx]

            # Convert to summary format
            items = []
            for rec in page_recs:
                # Extract formulation
                formulation_dict = rec.formulation

                # Check if multi-component or binary formulation
                if "components" in formulation_dict and formulation_dict["components"]:
                    # Multi-component formulation
                    components = [
                        ComponentData(
                            name=comp.get("name", "Unknown"),
                            role=comp.get("role", "Unknown"),
                            function=comp.get("function")
                        )
                        for comp in formulation_dict["components"]
                    ]
                    formulation = FormulationData(
                        components=components,
                        num_components=formulation_dict.get("num_components", len(components)),
                        molar_ratio=formulation_dict.get("molar_ratio", "Unknown")
                    )
                else:
                    # Binary formulation (backward compatible)
                    formulation = FormulationData(
                        HBD=formulation_dict.get("HBD", "Unknown"),
                        HBA=formulation_dict.get("HBA", "Unknown"),
                        molar_ratio=formulation_dict.get("molar_ratio", "Unknown")
                    )

                # Get performance score from experiment result
                performance_score = None
                if rec.experiment_result:
                    performance_score = rec.experiment_result.get_performance_score()

                summary = RecommendationSummary(
                    recommendation_id=rec.recommendation_id,
                    task_id=rec.task_id,
                    target_material=rec.task.get("target_material", "unknown"),
                    target_temperature=rec.task.get("target_temperature", 25.0),
                    formulation=formulation,
                    confidence=rec.confidence,
                    status=rec.status,
                    created_at=rec.created_at,
                    updated_at=rec.updated_at,
                    performance_score=performance_score
                )
                items.append(summary)

            # Build pagination info
            pagination = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
            }

            return RecommendationListData(
                items=items,
                pagination=pagination
            )

        except Exception as e:
            logger.error(f"Failed to list recommendations: {e}", exc_info=True)
            raise RuntimeError(f"Failed to list recommendations: {str(e)}")

    def get_recommendation_detail(self, recommendation_id: str) -> RecommendationDetail:
        """
        Get detailed information for a recommendation.

        Args:
            recommendation_id: Recommendation ID

        Returns:
            RecommendationDetail

        Raises:
            ValueError: If recommendation not found
            RuntimeError: If retrieval fails
        """
        logger.info(f"Getting recommendation detail: {recommendation_id}")

        try:
            # Get recommendation manager
            rec_manager = get_rec_manager()

            # Get recommendation
            rec = rec_manager.get_recommendation(recommendation_id)
            if not rec:
                raise ValueError(f"Recommendation {recommendation_id} not found")

            # Convert formulation
            formulation_dict = rec.formulation

            # Check if multi-component or binary formulation
            if "components" in formulation_dict and formulation_dict["components"]:
                # Multi-component formulation
                components = [
                    ComponentData(
                        name=comp.get("name", "Unknown"),
                        role=comp.get("role", "Unknown"),
                        function=comp.get("function")
                    )
                    for comp in formulation_dict["components"]
                ]
                formulation = FormulationData(
                    components=components,
                    num_components=formulation_dict.get("num_components", len(components)),
                    molar_ratio=formulation_dict.get("molar_ratio", "Unknown")
                )
            else:
                # Binary formulation (backward compatible)
                formulation = FormulationData(
                    HBD=formulation_dict.get("HBD", "Unknown"),
                    HBA=formulation_dict.get("HBA", "Unknown"),
                    molar_ratio=formulation_dict.get("molar_ratio", "Unknown")
                )

            # Convert trajectory
            trajectory_steps = []
            for step in rec.trajectory.steps:
                # Convert formulation in step if exists
                step_formulation = None
                if "formulation" in step and step["formulation"]:
                    f = step["formulation"]
                    # Multi-component formulation in a step
                    if isinstance(f, dict) and f.get("components"):
                        components = [
                            ComponentData(
                                name=comp.get("name", "Unknown"),
                                role=comp.get("role", "Unknown"),
                                function=comp.get("function")
                            )
                            for comp in (f.get("components") or [])
                            if isinstance(comp, dict)
                        ]
                        step_formulation = FormulationData(
                            components=components,
                            num_components=f.get("num_components", len(components)),
                            molar_ratio=f.get("molar_ratio", "Unknown")
                        )
                    else:
                        # Binary formulation (backward compatible)
                        step_formulation = FormulationData(
                            HBD=f.get("HBD", "Unknown") if isinstance(f, dict) else "Unknown",
                            HBA=f.get("HBA", "Unknown") if isinstance(f, dict) else "Unknown",
                            molar_ratio=f.get("molar_ratio", "Unknown") if isinstance(f, dict) else "Unknown"
                        )

                traj_step = TrajectoryStep(
                    action=step.get("action") or step.get("phase") or "unknown",
                    reasoning=step.get("reasoning", ""),
                    phase=step.get("phase"),
                    iteration=step.get("iteration"),
                    tool=step.get("tool"),
                    num_memories=step.get("num_memories"),
                    formulation=step_formulation,
                    result_summary=step.get("result_summary"),
                    observation=step.get("observation"),
                    knowledge_updated=step.get("knowledge_updated"),
                    key_insights=step.get("key_insights"),
                    information_gaps=step.get("information_gaps"),
                )
                trajectory_steps.append(traj_step)

            trajectory = Trajectory(
                steps=trajectory_steps,
                tool_calls=rec.trajectory.metadata.get("tool_calls", []),
                metadata=rec.trajectory.metadata or {}
            )

            # Convert experiment result if exists
            experiment_result = None
            if rec.experiment_result:
                exp = rec.experiment_result
                experiment_result = ExperimentResultData(
                    is_liquid_formed=exp.is_liquid_formed,
                    measurements=exp.measurements,
                    conditions=exp.conditions,
                    properties=exp.properties,
                    experimenter=exp.experimenter,
                    experiment_date=exp.experiment_date,
                    notes=exp.notes,
                    performance_score=exp.get_performance_score()
                )

            # Get memories_used from trajectory final_result
            memories_used_titles = rec.trajectory.final_result.get("memories_used", [])
            memories_used = []

            if memories_used_titles:
                try:
                    # Get agent to access memory bank
                    agent = get_agent()
                    for title in memories_used_titles:
                        memory = agent.memory.get_memory_by_title(title)
                        if memory:
                            memories_used.append(MemoryItemSummary(
                                title=memory.title,
                                description=memory.description,
                                content=memory.content,
                                is_from_success=memory.is_from_success
                            ))
                        else:
                            logger.warning(f"Memory with title '{title}' not found in ReasoningBank")
                except Exception as e:
                    logger.error(f"Failed to retrieve memory details: {e}")

            # Build detail
            detail = RecommendationDetail(
                recommendation_id=rec.recommendation_id,
                task=rec.task,
                formulation=formulation,
                reasoning=rec.reasoning,
                confidence=rec.confidence,
                supporting_evidence=rec.trajectory.final_result.get("supporting_evidence", []),
                memories_used=memories_used,
                status=rec.status,
                trajectory=trajectory,
                experiment_result=experiment_result,
                created_at=rec.created_at,
                updated_at=rec.updated_at
            )

            return detail

        except ValueError as e:
            # Re-raise ValueError (not found)
            raise
        except Exception as e:
            logger.error(f"Failed to get recommendation detail: {e}", exc_info=True)
            raise RuntimeError(f"Failed to get recommendation detail: {str(e)}")

    def cancel_recommendation(self, recommendation_id: str) -> Dict[str, Any]:
        """
        Cancel a recommendation.

        Args:
            recommendation_id: Recommendation ID

        Returns:
            Dict with updated recommendation info

        Raises:
            ValueError: If recommendation not found or already completed
            RuntimeError: If cancellation fails
        """
        logger.info(f"Cancelling recommendation: {recommendation_id}")

        try:
            # Get recommendation manager
            rec_manager = get_rec_manager()

            # Get recommendation
            rec = rec_manager.get_recommendation(recommendation_id)
            if not rec:
                raise ValueError(f"Recommendation {recommendation_id} not found")

            # Check if can be cancelled
            if rec.status == "COMPLETED":
                raise ValueError(
                    f"Cannot cancel recommendation {recommendation_id}: already completed"
                )

            if rec.status == "CANCELLED":
                raise ValueError(
                    f"Recommendation {recommendation_id} is already cancelled"
                )

            # Update status
            rec_manager.update_status(recommendation_id, "CANCELLED")

            # Get updated recommendation
            updated_rec = rec_manager.get_recommendation(recommendation_id)

            return {
                "recommendation_id": updated_rec.recommendation_id,
                "status": updated_rec.status,
                "updated_at": updated_rec.updated_at
            }

        except ValueError as e:
            # Re-raise ValueError
            raise
        except Exception as e:
            logger.error(f"Failed to cancel recommendation: {e}", exc_info=True)
            raise RuntimeError(f"Failed to cancel recommendation: {str(e)}")

    def get_statistics_fast(self, material: Optional[str] = None) -> Dict[str, int]:
        """
        Get lightweight statistics from index only (fast - no file I/O).

        Args:
            material: Optional material filter

        Returns:
            Dict with counts by status

        Raises:
            RuntimeError: If retrieval fails
        """
        logger.info(f"Getting fast statistics: material={material}")

        try:
            # Get recommendation manager
            rec_manager = get_rec_manager()

            # Get statistics from index only
            stats = rec_manager.get_statistics_fast(material=material)

            return stats

        except Exception as e:
            logger.error(f"Failed to get statistics: {e}", exc_info=True)
            raise RuntimeError(f"Failed to get statistics: {str(e)}")


# Singleton instance
_service: RecommendationService = None


def get_recommendation_service() -> RecommendationService:
    """Get recommendation service singleton"""
    global _service
    if _service is None:
        _service = RecommendationService()
    return _service
