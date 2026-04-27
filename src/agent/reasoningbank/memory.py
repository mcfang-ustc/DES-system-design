"""
Memory Item Data Structures for ReasoningBank

This module defines the core data structures for storing reasoning strategies
in the ReasoningBank framework.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime
import json

from ..utils.serialization import to_jsonable


@dataclass
class MemoryItem:
    """
    A single reasoning strategy extracted from agent experience.

    Each memory item represents a distilled, generalizable insight that can
    guide future decision-making. Unlike raw trajectories, memory items abstract
    away low-level execution details while preserving transferable reasoning patterns.

    Attributes:
        title: Concise identifier summarizing the core strategy (e.g., "Prioritize H-Bond Analysis")
        description: One-sentence summary of when/how to apply this memory
        content: 1-5 sentences describing the reasoning strategy in detail
        source_task_id: Optional identifier of the task that generated this memory
        is_from_success: Whether this memory was extracted from a successful (True) or failed (False) trajectory
        created_at: ISO timestamp of when this memory was created
        embedding: Optional vector embedding for semantic similarity search
        metadata: Additional key-value pairs for filtering and organization
    """

    title: str
    description: str
    content: str
    source_task_id: Optional[str] = None
    is_from_success: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    embedding: Optional[List[float]] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """Validate memory item fields"""
        if not self.title or not self.title.strip():
            raise ValueError("Memory title cannot be empty")
        if not self.description or not self.description.strip():
            raise ValueError("Memory description cannot be empty")
        if not self.content or not self.content.strip():
            raise ValueError("Memory content cannot be empty")

    def to_dict(self) -> dict:
        """Convert memory item to dictionary for serialization"""
        return {
            "title": self.title,
            "description": self.description,
            "content": self.content,
            "source_task_id": self.source_task_id,
            "is_from_success": self.is_from_success,
            "created_at": self.created_at,
            "embedding": to_jsonable(self.embedding),
            "metadata": to_jsonable(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryItem":
        """Create memory item from dictionary"""
        return cls(
            title=data["title"],
            description=data["description"],
            content=data["content"],
            source_task_id=data.get("source_task_id"),
            is_from_success=data.get("is_from_success", True),
            created_at=data.get("created_at", datetime.now().isoformat()),
            embedding=data.get("embedding"),
            metadata=data.get("metadata", {}),
        )

    def to_prompt_string(self) -> str:
        """
        Format memory item for injection into agent's system prompt.

        Returns:
            Formatted string suitable for LLM context
        """
        return f"**{self.title}**\n{self.content}"

    def to_detailed_string(self) -> str:
        """
        Format memory item with full details for debugging/logging.

        Returns:
            Detailed multi-line string representation
        """
        origin = "Success" if self.is_from_success else "Failure"
        return (
            f"# {self.title}\n"
            f"## Description\n{self.description}\n"
            f"## Content\n{self.content}\n"
            f"## Metadata\n"
            f"- Origin: {origin}\n"
            f"- Source Task: {self.source_task_id or 'N/A'}\n"
            f"- Created: {self.created_at}\n"
        )


@dataclass
class MemoryQuery:
    """
    Query object for retrieving relevant memories from ReasoningBank.

    Attributes:
        query_text: Natural language description of the current task
        top_k: Number of most relevant memories to retrieve (default: 3)
        filters: Optional filters to apply (e.g., {"is_from_success": True})
        min_similarity: Minimum similarity threshold (0.0 to 1.0)
    """

    query_text: str
    top_k: int = 3
    filters: dict = field(default_factory=dict)
    min_similarity: float = 0.0


@dataclass
class Trajectory:
    """
    Represents an agent's interaction history for one task.

    Attributes:
        task_id: Unique identifier for the task
        task_description: Natural language description of the task
        steps: List of agent actions and observations
        outcome: "success" or "failure"
        final_result: The agent's final output
        metadata: Additional information (e.g., tool calls, reasoning traces)
    """

    task_id: str
    task_description: str
    steps: List[dict]
    outcome: str
    final_result: dict
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert trajectory to a JSON-safe dictionary (no deepcopy)."""
        return {
            "task_id": self.task_id,
            "task_description": self.task_description,
            "steps": to_jsonable(self.steps),
            "outcome": self.outcome,
            "final_result": to_jsonable(self.final_result),
            "metadata": to_jsonable(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trajectory":
        """Create trajectory from dictionary"""
        return cls(**data)


# Example usage and validation
if __name__ == "__main__":
    # Create a sample memory item
    memory = MemoryItem(
        title="Prioritize Hydrogen Bond Network Analysis",
        description="When designing DES for polar materials, analyze H-bond strength first",
        content=(
            "For dissolving polar polymers like cellulose, the hydrogen bond donating/accepting "
            "capability of DES components is the primary factor. Use CoreRAG to retrieve H-bond "
            "parameters before exploring molar ratios."
        ),
        source_task_id="task_001",
        is_from_success=True,
        metadata={"domain": "polymer_dissolution", "material_type": "cellulose"},
    )

    # Test serialization
    memory_dict = memory.to_dict()
    print("Serialized memory:")
    print(json.dumps(memory_dict, indent=2))

    # Test deserialization
    reconstructed = MemoryItem.from_dict(memory_dict)
    assert reconstructed.title == memory.title
    print("\nReconstruction successful!")

    # Test prompt formatting
    print("\nPrompt format:")
    print(memory.to_prompt_string())

    # Test detailed formatting
    print("\nDetailed format:")
    print(memory.to_detailed_string())
