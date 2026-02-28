"""
Feedback System for Asynchronous Experiment-Based Optimization

This module implements the feedback loop for real experimental validation:
- ExperimentResult: Real experiment data (not LLM evaluation)
- Recommendation: Persistent recommendation records
- RecommendationManager: Storage and retrieval
- FeedbackProcessor: Process experimental feedback and update ReasoningBank
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Callable
from pathlib import Path
from datetime import datetime
import json
import logging
import os
import threading

from .memory import Trajectory, MemoryItem
from .extractor import format_experiment_for_llm

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """
    Real experimental feedback data for DES formulation.

    This replaces LLM-as-a-Judge with actual lab measurements.

    Required Fields:
        is_liquid_formed (bool): Whether the DES components dissolved to form liquid

    Optional Fields:
        measurements (list): Long-table leaching measurements across targets/timepoints
        properties (dict): User-defined additional measurements (viscosity, density, etc.)
        conditions (dict): Shared experimental conditions (temperature_C, solid_liquid_ratio)
        experimenter (str): Who performed the experiment
        experiment_date (str): When the experiment was conducted
        notes (str): Experimental notes
    """

    # ===== Required Fields =====
    is_liquid_formed: bool

    # ===== Optional Fields =====
    properties: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)
    measurements: List[Dict[str, Any]] = field(default_factory=list)

    # ===== Metadata =====
    experimenter: Optional[str] = None
    experiment_date: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: str = ""

    def __post_init__(self):
        """Validate data integrity with boundary case handling"""
        # Require measurements if DES formed
        if self.is_liquid_formed:
            has_measurement_eff = any(
                (m.get("leaching_efficiency") is not None) for m in (self.measurements or [])
            )
            if not has_measurement_eff:
                raise ValueError(
                    "When is_liquid_formed=True, provide at least one measurement.leaching_efficiency"
                )

    def get_performance_score(self) -> float:
        """
        Calculate performance score (0-10) for ranking and comparison.

        Rules:
        - DES not formed: 0.0
        - DES formed: based on leaching efficiency (higher is better)

        Note:
        - `measurements[].leaching_efficiency` may be reported in different units
          (commonly "%" for leaching efficiency, occasionally g/L for solubility-like tasks).
        - This function returns a *normalized* 0-10 score for UI sorting and comparisons.

        Returns:
            float: Performance score (0-10)
        """
        if not self.is_liquid_formed:
            return 0.0

        measurements = self.measurements or []
        rows = [
            m for m in measurements
            if m.get("leaching_efficiency") is not None
        ]

        if rows:
            max_eff = max(float(m.get("leaching_efficiency")) for m in rows)

            # Heuristic unit handling:
            # - If unit is percent-like, map 0-100% -> 0-10 (divide by 10).
            # - Otherwise, keep legacy behavior (cap raw value at 10).
            unit = ""
            for m in rows:
                u = (m.get("unit") or "").strip()
                if u:
                    unit = u
                    break

            unit_norm = unit.lower()
            is_percent = unit_norm in {"%", "percent", "pct", "percentage"}

            if is_percent:
                return max(0.0, min(10.0, max_eff / 10.0))

            return max(0.0, min(10.0, max_eff))

        # Default to medium score if no efficiency data
        return 5.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentResult":
        """Create from dictionary"""
        return cls(**data)


@dataclass
class Recommendation:
    """
    Persistent record of a DES formulation recommendation.

    This stores all information needed for:
    - User review and experimentation
    - Feedback submission
    - Cross-instance reuse (system A → system B)

    Attributes:
        recommendation_id: Unique identifier
        task: Original task specification
        task_id: Task identifier
        formulation: Recommended DES formulation
        reasoning: Agent's reasoning
        confidence: Confidence score (0-1)
        trajectory: Complete agent execution trace
        status: Current status (PENDING, COMPLETED, CANCELLED)
        experiment_result: Experimental feedback (None until submitted)
        version: Data format version (for backward compatibility)
    """

    # ===== Core Fields =====
    recommendation_id: str
    task: Dict
    task_id: str
    formulation: Dict  # {HBD, HBA, molar_ratio}
    reasoning: str
    confidence: float

    # ===== Trajectory (for cross-instance reuse) =====
    trajectory: Trajectory

    # ===== Status Management =====
    status: str  # PENDING, COMPLETED, CANCELLED
    created_at: str
    updated_at: str

    # ===== Experimental Feedback =====
    experiment_result: Optional[ExperimentResult] = None

    # ===== Versioning (for backward compatibility) =====
    version: str = "1.0"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        data = asdict(self)
        # Special handling for Trajectory
        data["trajectory"] = self.trajectory.to_dict()
        # Special handling for ExperimentResult
        if self.experiment_result:
            data["experiment_result"] = self.experiment_result.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Recommendation":
        """
        Create from dictionary with version support.

        Supports backward compatibility for future format changes.
        """
        version = data.get("version", "1.0")

        if version == "1.0":
            # Reconstruct Trajectory
            data["trajectory"] = Trajectory.from_dict(data["trajectory"])
            # Reconstruct ExperimentResult
            if data.get("experiment_result"):
                data["experiment_result"] = ExperimentResult.from_dict(
                    data["experiment_result"]
                )
            return cls(**data)
        else:
            raise ValueError(f"Unsupported data format version: {version}")


class RecommendationManager:
    """
    Manages persistent storage and retrieval of DES formulation recommendations.

    Storage Strategy:
    - Phase 1: JSON files (one per recommendation) + index.json
    - Advantages: Simple, debuggable, Git-compatible, easy migration
    - Directory structure:
        data/recommendations/
        ├── index.json
        ├── REC_20251016_001.json
        ├── REC_20251016_002.json
        └── ...

    Methods:
        save_recommendation: Persist recommendation to disk
        get_recommendation: Load recommendation by ID
        list_recommendations: Query recommendations with filters
        update_status: Update recommendation status
        submit_feedback: Submit experimental feedback
        get_statistics: Get summary statistics
    """

    def __init__(self, storage_path: str = "data/recommendations"):
        """
        Initialize RecommendationManager.

        Args:
            storage_path: Directory path for storing recommendations
        """
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storage_path / "index.json"
        # Protect index updates + file writes from concurrent access (e.g. async feedback workers).
        self._lock = threading.RLock()
        self._load_index()
        logger.info(f"Initialized RecommendationManager at {self.storage_path}")

    def _load_index(self):
        """Load recommendation index"""
        with self._lock:
            if self.index_file.exists():
                with open(self.index_file, "r", encoding="utf-8") as f:
                    self.index = json.load(f)
                logger.debug(f"Loaded index with {len(self.index)} entries")
            else:
                self.index = {}
                logger.debug("Created new index")

    def _save_index(self):
        """Save recommendation index"""
        with self._lock:
            # Atomic write to avoid partial/corrupted index.json during crashes or concurrent writes.
            tmp_path = self.index_file.with_suffix(self.index_file.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.index, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.index_file)
            logger.debug(f"Saved index with {len(self.index)} entries")

    def _get_formulation_summary(self, formulation: Dict) -> str:
        """
        Generate formulation summary string for list display.

        Args:
            formulation: Formulation dictionary

        Returns:
            Human-readable formulation string
        """
        if "components" in formulation and formulation.get("components"):
            # Multi-component formulation
            names = [c.get("name", "Unknown") for c in formulation["components"]]
            molar_ratio = formulation.get("molar_ratio", "?")
            return f"{' + '.join(names)} ({molar_ratio})"
        else:
            # Binary formulation
            hbd = formulation.get("HBD", "?")
            hba = formulation.get("HBA", "?")
            molar_ratio = formulation.get("molar_ratio", "?")
            return f"{hbd} : {hba} ({molar_ratio})"

    def save_recommendation(self, rec: Recommendation) -> str:
        """
        Save recommendation to disk.

        Args:
            rec: Recommendation object

        Returns:
            str: Recommendation ID
        """
        with self._lock:
            rec_file = self.storage_path / f"{rec.recommendation_id}.json"

            # Save recommendation (atomic write)
            tmp_path = rec_file.with_suffix(rec_file.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, rec_file)

            # Update index with extended fields for fast list access
            self.index[rec.recommendation_id] = {
                "task_id": rec.task_id,
                "status": rec.status,
                "created_at": rec.created_at,
                "updated_at": rec.updated_at,
                "target_material": rec.task.get("target_material"),
                "target_temperature": rec.task.get("target_temperature"),
                # New fields for list view (v2)
                "formulation_summary": self._get_formulation_summary(rec.formulation),
                "formulation": rec.formulation,  # Store full formulation dict
                "confidence": rec.confidence,
                "performance_score": rec.experiment_result.get_performance_score() if rec.experiment_result else None,
                "file": str(rec_file),
            }
            self._save_index()

            logger.info(
                f"Saved recommendation {rec.recommendation_id} with status {rec.status}"
            )
            return rec.recommendation_id

    def get_recommendation(self, rec_id: str) -> Optional[Recommendation]:
        """
        Get recommendation by ID.

        Args:
            rec_id: Recommendation ID

        Returns:
            Recommendation object or None if not found
        """
        with self._lock:
            if rec_id not in self.index:
                logger.warning(f"Recommendation {rec_id} not found in index")
                return None

            rec_file = Path(self.index[rec_id]["file"])
            if not rec_file.exists():
                logger.error(f"Recommendation file not found: {rec_file}")
                return None

            with open(rec_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            return Recommendation.from_dict(data)

    def list_recommendations(
        self,
        status: Optional[str] = None,
        target_material: Optional[str] = None,
        limit: int = 100,
    ) -> List[Recommendation]:
        """
        Query recommendations with filters.

        Args:
            status: Filter by status (PENDING, COMPLETED, CANCELLED)
            target_material: Filter by target material
            limit: Maximum number of results

        Returns:
            List of Recommendation objects
        """
        filtered = []

        with self._lock:
            index_items = list(self.index.items())

        for rec_id, meta in index_items:
            # Apply filters
            if status and meta["status"] != status:
                continue
            if target_material and meta.get("target_material") != target_material:
                continue

            # Load recommendation
            rec = self.get_recommendation(rec_id)
            if rec:
                filtered.append(rec)

            if len(filtered) >= limit:
                break

        # Sort by creation time (descending)
        filtered.sort(key=lambda r: r.created_at, reverse=True)

        logger.debug(
            f"Listed {len(filtered)} recommendations "
            f"(status={status}, material={target_material})"
        )

        return filtered

    def update_status(self, rec_id: str, status: str):
        """
        Update recommendation status.

        Args:
            rec_id: Recommendation ID
            status: New status (PENDING, COMPLETED, CANCELLED)
        """
        with self._lock:
            rec = self.get_recommendation(rec_id)
            if not rec:
                raise ValueError(f"Recommendation {rec_id} not found")

            rec.status = status
            rec.updated_at = datetime.now().isoformat()
            self.save_recommendation(rec)

            logger.info(f"Updated {rec_id} status to {status}")

    def submit_feedback(self, rec_id: str, experiment_result: ExperimentResult):
        """
        Submit experimental feedback for a recommendation.

        Args:
            rec_id: Recommendation ID
            experiment_result: ExperimentResult object
        """
        with self._lock:
            rec = self.get_recommendation(rec_id)
            if not rec:
                raise ValueError(f"Recommendation {rec_id} not found")

            rec.experiment_result = experiment_result
            rec.status = "COMPLETED"
            rec.updated_at = datetime.now().isoformat()

            self.save_recommendation(rec)
            logger.info(f"Submitted experimental feedback for {rec_id}")

    def get_statistics(self) -> Dict:
        """
        Get summary statistics.

        Returns:
            Dict with statistics
        """
        with self._lock:
            stats = {"total": len(self.index), "by_status": {}, "by_material": {}}

            for meta in self.index.values():
                # Count by status
                status = meta["status"]
                stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

                # Count by material
                material = meta.get("target_material", "unknown")
                stats["by_material"][material] = stats["by_material"].get(material, 0) + 1

            return stats

    def get_statistics_fast(self, material: Optional[str] = None) -> Dict[str, int]:
        """
        Get lightweight statistics from index only (no file I/O).

        Args:
            material: Optional material filter

        Returns:
            Dict with counts by status
        """
        stats = {
            "all": 0,
            "GENERATING": 0,
            "PENDING": 0,
            "COMPLETED": 0,
            "FAILED": 0,
            "CANCELLED": 0
        }

        with self._lock:
            for rec_id, meta in self.index.items():
                # Apply material filter if specified
                if material and meta.get("target_material") != material:
                    continue

                stats["all"] += 1
                status = meta["status"]
                if status in stats:
                    stats[status] += 1

        logger.debug(f"Fast statistics: {stats} (material={material})")
        return stats

    def list_recommendations_fast(
        self,
        status: Optional[str] = None,
        target_material: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Fast list recommendations using index only (no file I/O).

        Args:
            status: Filter by status
            target_material: Filter by material
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Dict with items (index metadata) and pagination info
        """
        # Filter from index
        filtered = []
        with self._lock:
            for rec_id, meta in self.index.items():
                if status and meta["status"] != status:
                    continue
                if target_material and meta.get("target_material") != target_material:
                    continue

                # Add rec_id to meta for response
                filtered.append({**meta, "recommendation_id": rec_id})

        # Sort by created_at (descending)
        filtered.sort(key=lambda x: x["created_at"], reverse=True)

        # Pagination
        total = len(filtered)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = filtered[start_idx:end_idx]

        logger.debug(
            f"Fast list: {len(page_items)}/{total} items "
            f"(status={status}, material={target_material}, page={page})"
        )

        return {
            "items": page_items,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 1
            }
        }


class FeedbackProcessor:
    """
    Processes experimental feedback and updates ReasoningBank.

    This component bridges the asynchronous feedback loop:
    1. Load recommendation + experiment result
    2. Update trajectory (no binary success/failure, just "experiment_completed")
    3. Extract data-driven memories using MemoryExtractor
    4. Consolidate to ReasoningBank

    Key Design:
    - No binary classification (removed success/failure)
    - Extract "formulation-condition-performance" mappings
    - All memories are experiment-validated
    """

    def __init__(self, agent, rec_manager: RecommendationManager):
        """
        Initialize FeedbackProcessor.

        Args:
            agent: DESAgent instance (for extractor and memory access)
            rec_manager: RecommendationManager instance
        """
        self.agent = agent
        self.rec_manager = rec_manager
        logger.info("Initialized FeedbackProcessor")

    def process_feedback(self, rec_id: str, is_update: bool = False) -> Dict:
        """
        Process experimental feedback for one recommendation.

        Args:
            rec_id: Recommendation ID
            is_update: If True, delete old memories before extracting new ones

        Returns:
            Dict with processing results
        """
        # 1. Load recommendation and feedback
        rec = self.rec_manager.get_recommendation(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found")

        if not rec.experiment_result:
            raise ValueError(f"No experiment result for {rec_id}")

        logger.info(f"Processing feedback for {rec_id} (is_update={is_update})")

        # 2. If updating, delete old memories first
        deleted_count = 0
        if is_update:
            deleted_count = self.agent.memory.delete_by_recommendation_id(rec_id)
            logger.info(f"Deleted {deleted_count} old memories before re-extraction")

        # 3. Update Trajectory (no binary outcome, unified as "experiment_completed")
        exp_result = rec.experiment_result
        rec.trajectory.outcome = "experiment_completed"

        # Add experiment data to trajectory metadata
        rec.trajectory.metadata["experiment_result"] = exp_result.to_dict()
        exp_summary_text = format_experiment_for_llm(exp_result)
        rec.trajectory.metadata["experiment_summary_text"] = exp_summary_text
        # Note: performance_score removed - use raw leaching efficiency instead
        rec.trajectory.metadata["feedback_processed_at"] = datetime.now().isoformat()
        if is_update:
            rec.trajectory.metadata["feedback_updated_at"] = datetime.now().isoformat()

        # 4. Extract experiment-based memories
        logger.info(f"Extracting experiment-based memories (is_update={is_update})")
        new_memories = self.agent.extractor.extract_from_experiment(
            rec.trajectory, exp_result
        )

        # Tag memories as experiment-validated
        for memory in new_memories:
            memory.metadata["source"] = "experiment_validated"
            memory.metadata["recommendation_id"] = rec_id
            # Key experimental parameters (use raw metrics, not performance_score)
            memory.metadata["is_liquid_formed"] = exp_result.is_liquid_formed
            memory.metadata["measurements"] = exp_result.measurements
            memory.metadata["experiment_date"] = exp_result.experiment_date
            memory.metadata["experiment_summary_text"] = exp_summary_text
            if is_update:
                memory.metadata["is_updated"] = True

        # 5. Consolidate to ReasoningBank
        if new_memories:
            self.agent.memory.consolidate(new_memories)
            logger.info(
                f"Consolidated {len(new_memories)} experiment-validated memories"
            )

            # Auto-save if configured
            if self.agent.config.get("memory", {}).get("auto_save", False):
                save_path = self.agent.config["memory"]["persist_path"]
                self.agent.memory.save(save_path)
                logger.info(f"Auto-saved memory bank to {save_path}")

        # 6. Save updated recommendation
        self.rec_manager.save_recommendation(rec)

        result = {
            "recommendation_id": rec_id,
            "is_liquid_formed": exp_result.is_liquid_formed,
            "memories_extracted": [m.title for m in new_memories],
            "num_memories": len(new_memories),
            "measurement_count": len(exp_result.measurements),
            "experiment_summary_text": exp_summary_text,
        }

        if is_update:
            result["deleted_memories"] = deleted_count
            result["is_update"] = True

        return result

    def process_all_pending_feedback(self) -> List[Dict]:
        """
        Process all COMPLETED recommendations that haven't been processed yet.

        Returns:
            List of processing results
        """
        completed_recs = self.rec_manager.list_recommendations(status="COMPLETED")

        results = []
        for rec in completed_recs:
            # Check if already processed
            if rec.trajectory.metadata.get("feedback_processed_at"):
                logger.debug(f"Feedback for {rec.recommendation_id} already processed")
                continue

            try:
                result = self.process_feedback(rec.recommendation_id)
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Failed to process feedback for {rec.recommendation_id}: {e}"
                )

        logger.info(f"Processed {len(results)} pending feedbacks")
        return results


# Example usage
if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.INFO)

    # Create temp storage
    temp_dir = tempfile.mkdtemp()
    rec_manager = RecommendationManager(temp_dir)

    # Create sample recommendation
    from .memory import Trajectory

    traj = Trajectory(
        task_id="task_001",
        task_description="Design DES for cellulose",
        steps=[],
        outcome="pending",
        final_result={
            "formulation": {"HBD": "Urea", "HBA": "ChCl", "molar_ratio": "1:2"}
        },
        metadata={},
    )

    rec = Recommendation(
        recommendation_id="REC_TEST_001",
        task={"target_material": "cellulose", "target_temperature": 25},
        task_id="task_001",
        formulation={"HBD": "Urea", "HBA": "ChCl", "molar_ratio": "1:2"},
        reasoning="Good H-bond network",
        confidence=0.8,
        trajectory=traj,
        status="PENDING",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    # Save recommendation
    rec_manager.save_recommendation(rec)

    # Submit feedback
    exp_result = ExperimentResult(
        is_liquid_formed=True,
        measurements=[{"target_material": "cellulose", "time_h": 6, "leaching_efficiency": 6.5, "unit": "g/L"}],
        properties={"viscosity": 450},
        experimenter="Dr. Test",
        notes="Good performance",
    )

    rec_manager.submit_feedback("REC_TEST_001", exp_result)

    # Load and verify
    loaded_rec = rec_manager.get_recommendation("REC_TEST_001")
    print(f"Loaded: {loaded_rec.recommendation_id}")
    print(f"Status: {loaded_rec.status}")
    print(f"Performance: {loaded_rec.experiment_result.get_performance_score():.1f}/10")

    # Statistics
    stats = rec_manager.get_statistics()
    print(f"Statistics: {stats}")
