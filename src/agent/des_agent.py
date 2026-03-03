"""
DES Formulation Agent with ReasoningBank

This module implements the main agent for DES formulation design,
integrating ReasoningBank memory system with CoreRAG and LargeRAG tools.
"""

from typing import Dict, List, Optional, Callable, Tuple, Any
import logging
from datetime import datetime
import asyncio
import json
import re

from .reasoningbank import (
    ReasoningBank,
    MemoryRetriever,
    MemoryExtractor,
    LLMJudge,
    MemoryItem,
    MemoryQuery,
    Trajectory,
    format_memories_for_prompt,
    # New: Async feedback components
    RecommendationManager,
    FeedbackProcessor,
    Recommendation,
    ExperimentResult
)

from .prompts import (
    OBSERVE_PROMPT,
    format_action_result_for_observe,
    parse_observe_output
)
from .utils.serialization import to_jsonable
from .utils.json_extract import loads_json_from_text
from .utils.formulation_validation import (
    normalize_formulation,
    validate_formulation,
    summarize_formulation,
)

logger = logging.getLogger(__name__)


class DESAgent:
    """
    Main agent for DES formulation design with asynchronous experimental feedback.

    NEW: The agent now supports real experimental feedback loop:
    1. Retrieve relevant memories from ReasoningBank
    2. Query CoreRAG for theoretical knowledge
    3. Query LargeRAG for literature precedents
    4. Generate DES formulation with reasoning
    5. Create persistent Recommendation record (status: PENDING)
    6. [Async] User performs experiment
    7. [Async] User submits ExperimentResult
    8. Extract data-driven memories and consolidate

    Attributes:
        llm_client: LLM for agent reasoning
        reasoning_bank: ReasoningBank instance
        retriever: MemoryRetriever instance
        extractor: MemoryExtractor instance
        judge: LLMJudge instance (optional, not used in v1)
        rec_manager: RecommendationManager for persistent storage
        feedback_processor: FeedbackProcessor for async feedback
        corerag_client: CoreRAG tool interface
        largerag_client: LargeRAG tool interface
        config: Configuration dictionary
    """

    def __init__(
        self,
        llm_client: Callable[[str], str],
        reasoning_bank: ReasoningBank,
        retriever: MemoryRetriever,
        extractor: MemoryExtractor,
        judge: LLMJudge,
        rec_manager: RecommendationManager,  # NEW: Required
        corerag_client: Optional[object] = None,
        largerag_client: Optional[object] = None,
        config: Optional[Dict] = None
    ):
        """
        Initialize DESAgent with async feedback support.

        Args:
            llm_client: Function for LLM calls
            reasoning_bank: ReasoningBank instance
            retriever: MemoryRetriever instance
            extractor: MemoryExtractor instance
            judge: LLMJudge instance (optional, for future use)
            rec_manager: RecommendationManager instance (NEW)
            corerag_client: CoreRAG tool (optional)
            largerag_client: LargeRAG tool (optional)
            config: Configuration dictionary
        """
        self.llm_client = llm_client
        self.memory = reasoning_bank
        self.retriever = retriever
        self.extractor = extractor
        self.judge = judge
        self.corerag = corerag_client
        self.largerag = largerag_client
        self.config = config or {}

        # NEW: Recommendation and feedback management
        self.rec_manager = rec_manager
        self.feedback_processor = FeedbackProcessor(self, rec_manager)

        logger.info("Initialized DESAgent with async experimental feedback support")

    # ===== ReAct Core Methods =====

    def _think(self, task: Dict, knowledge_state: Dict, iteration: int) -> Dict:
        """
        THINK phase: Analyze current knowledge state and decide next action.

        This is the planning/reasoning step where the LLM examines what information
        has been gathered so far and decides what to do next.

        Args:
            task: Task specification
            knowledge_state: Current accumulated knowledge
            iteration: Current iteration number

        Returns:
            Dict with:
                - action: Next action to take
                - reasoning: Explanation of the decision
                - information_gaps: What information is still missing
        """
        # Build thinking prompt
        max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
        remaining_iterations = max_iterations - iteration
        progress_pct = int((iteration / max_iterations) * 100)

        # Determine iteration stage
        if progress_pct < 40:
            stage = "Early"
        elif progress_pct < 75:
            stage = "Mid"
        else:
            stage = "Late"

        # Summarize accumulated knowledge
        theory_summary = f"{knowledge_state['num_theory_queries']} queries made" if knowledge_state['num_theory_queries'] > 0 else "Not retrieved"
        literature_summary = f"{knowledge_state['num_literature_queries']} queries made" if knowledge_state['num_literature_queries'] > 0 else "Not retrieved"

        # NEW: Failure tracking summary
        failed_theory = knowledge_state.get('failed_theory_attempts', 0)
        failed_literature = knowledge_state.get('failed_literature_attempts', 0)

        # Format memory summary
        memory_summary = ""
        if knowledge_state['memories'] and len(knowledge_state['memories']) > 0:
            memory_summary = "\n**Retrieved Memories Summary**:\n"
            for i, mem in enumerate(knowledge_state['memories'][:3], 1):
                measurements = mem.metadata.get("measurements", [])
                max_eff = None
                unit = ""
                for m in measurements:
                    if m.get("leaching_efficiency") is not None:
                        max_eff = m.get("leaching_efficiency") if max_eff is None else max(max_eff, m.get("leaching_efficiency"))
                        unit = m.get("unit", unit)
                eff_text = f"浸出效率≈{max_eff} {unit}" if max_eff is not None else "无浸出数据"
                memory_summary += f"  {i}. {mem.title[:80]}... ({eff_text})\n"
            if len(knowledge_state['memories']) > 3:
                memory_summary += f"  ... and {len(knowledge_state['memories']) - 3} more\n"

        # ===== P2: Budgets + stagnation signals (used for prompts and policy guards) =====
        agent_cfg = self.config.get("agent", {}) or {}
        max_corerag = int(agent_cfg.get("max_corerag_queries", 3))
        max_largerag = int(agent_cfg.get("max_largerag_queries", 8))
        max_parallel = int(agent_cfg.get("max_parallel_queries", 3))

        expected_num_components = int(task.get("num_components") or 2)
        num_valid_candidates = self._count_valid_candidates(
            knowledge_state.get("formulation_candidates") or [],
            expected_num_components,
        )
        stagnating = self._is_stagnating(knowledge_state.get("observations") or [])

        final_round_note = ""
        if remaining_iterations <= 2:
            final_round_note = (
                "**Time Note**: You are in the final rounds. Prefer generating a complete, testable formulation "
                "and finishing over additional searching unless there is a single critical missing detail.\n"
            )

        think_prompt = f"""You are a DES (Deep Eutectic Solvent) formulation expert planning your research approach.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Target Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Stage**: **{stage}**
{final_round_note}

**Current Knowledge State**:
- Memories retrieved: {knowledge_state['memories_retrieved']} ({len(knowledge_state['memories'] or [])} items)
{memory_summary}
- Theoretical knowledge (CoreRAG): {theory_summary} (failed attempts: {failed_theory})
- Literature knowledge (LargeRAG): {literature_summary} (failed attempts: {failed_literature})
- Formulation candidates generated: {len(knowledge_state['formulation_candidates'])} (valid: {num_valid_candidates})
- Previous observations: {len(knowledge_state['observations'])}
- query_parallel executed: {int(knowledge_state.get('num_parallel_queries', 0))} (budget: {max_parallel})
- Stagnation detected: {stagnating}

**Tool Budgets (hard caps)**:
- CoreRAG queries: max {max_corerag}
- LargeRAG queries: max {max_largerag}
- query_parallel actions: max {max_parallel}

**Recent Observations**:
{self._format_observations(knowledge_state['observations'][-2:] if len(knowledge_state['observations']) > 0 else [])}

**Latest OBSERVE Analysis** (from previous iteration):
{self._format_latest_observe_recommendation(knowledge_state['observations'])}

**Available Actions**:
1. **retrieve_memories** - Get past experiences from ReasoningBank (validated experimental data). NOTE: If returned empty in last iteration, you may skip and proceed with other tools.
2. **query_literature** - Query LargeRAG for literature data (empirical recipes, conditions, performance)
3. **query_theory** - Query CoreRAG ontology for theoretical principles (mechanistic/design-rule gaps; expensive)
4. **generate_formulation** - Generate DES formulation from accumulated knowledge
5. **refine_formulation** - Refine existing formulation with more information
6. **finish** - Complete task (only if formulation is ready)
7. **query_parallel** - Query both CoreRAG and LargeRAG simultaneously (RARE: only when you explicitly need BOTH and budgets allow)

**Tool Characteristics**:
- **ReasoningBank (retrieve_memories)**: Instant retrieval of validated past experiments - **MOST RELIABLE WHEN AVAILABLE**. If empty, no relevant memories exist - this is acceptable, proceed with other tools.
- **LargeRAG (query_literature)**: Fast vector search (~1-2 seconds) across 10,000+ papers
- **CoreRAG (query_theory)**: Deep ontology reasoning (~5-10 minutes per query) - use sparingly and only for specific mechanistic/design-rule gaps

**Research Workflow (coarse-to-fine, stop early when done)**:
1) **Global map**: Use at most ONE broad query to map the design space (literature OR theory). Use query_parallel only if BOTH are missing and you need a fast global overview.
2) **Gap-driven deepening**: Each additional query must target ONE concrete information gap. Avoid repeating broad queries.
3) **Formulate + refine**: Once you have at least two knowledge sources (e.g., memories + literature, or theory + literature), generate a candidate and iterate on it.
4) **Stop**: If new queries are not adding new key insights / information gaps are repeating, stop searching and produce the best testable formulation.

**Note: Use Memory to Guide Theory and Literature Queries**:
1. **retrieve memories first** (in iteration 1) if not yet retrieved - memories contain validated experimental data
2. **If retrieve_memories returns 0 results**: This is ACCEPTABLE - no relevant historical data exists. Immediately move on to theory/literature queries without retrying.
3. Memories from real experiments are the **MOST RELIABLE** knowledge source when available
4. Only query CoreRAG/LargeRAG if memories are insufficient or missing critical details

**Research Requirements**:
- **Preferred**: Memories + Theory (CoreRAG) + Literature (LargeRAG)
- **Acceptable**: Memories + LLM parametric knowledge (if tools unavailable)
- **Minimum**: Theory + Literature (if no relevant memories exist)
- **Fallback**: LLM parametric knowledge (if all tools fail)

**CoreRAG Usage Guidelines**:
- CoreRAG is NECESSARY (DES design needs theoretical basis), but takes 5-10 minutes
- **Use thoughtfully**: Craft comprehensive, well-structured queries to maximize information gain per query
- **Avoid repeated similar queries**: Plan what theoretical knowledge you need, then query ONCE with a complete question
- Good query: "What are the key principles for cellulose dissolution via DES? Include hydrogen bonding mechanisms, component selection criteria, and molar ratio considerations."
- Poor query: Multiple narrow queries like "What is hydrogen bonding?" then "What about molar ratios?" (wasteful)

**Parallel Query Policy (IMPORTANT)**:
- **query_parallel is NOT the default.** Use it only when you explicitly need BOTH (mechanistic theory + empirical recipes) AND at least one of them is still missing.
- If you already have both theory and literature, prefer generate_formulation/refine_formulation instead of query_parallel.

**Decision Guidelines by Stage**:
- **Early ({stage == 'Early' and '✓' or '✗'})**:
  - **Priority 1**: Retrieve memories if not yet done. If returns 0, immediately proceed to Priority 2.
  - **Priority 2**: Query literature/theory (memories empty OR insufficient)
- **Mid ({stage == 'Mid' and '✓' or '✗'})**: Ensure sufficient knowledge from available sources (memories when available + tools)
- **Late ({stage == 'Late' and '✓' or '✗'})**: Must generate formulation soon. If you have any knowledge sources (memories/theory/literature) → generate now

**STRICT Anti-Loop Rules**:
- **STOP after 2 consecutive failures**: If a tool fails 2 times in a row → STOP trying, move to alternative action
- **Failed tool tracking**: CoreRAG failed {failed_theory} times, LargeRAG failed {failed_literature} times
- **If both tools unavailable ({failed_theory >= 2 and failed_literature >= 2})**: RELY ON MEMORIES (if available) + LLM parametric knowledge and generate formulation immediately
- **DO NOT repeat the same action if result is unchanged**:
  * If retrieve_memories returns 0 results → This means NO historical data exists. DO NOT retry. Immediately move to theory/literature queries.
  * If a query returns empty/unchanged results twice → move to alternative action
- **Stage awareness**: Prioritize actions that move towards formulation generation and completion (especially in Late stage)

**Your Task**:
Analyze the knowledge state and decide the SINGLE most valuable next action. You do NOT need to use all iterations; stop early if information is sufficient or new queries are not adding value.

Output JSON:
{{
    "action": "action_name",
    "reasoning": "Why this action is the best next step (2-3 sentences)",
    "information_gaps": ["gap1", "gap2"]  // What critical info is still missing
}}
"""

        try:
            response = self.llm_client(think_prompt)
            thought = self._parse_json_response(response)

            # Validate action
            valid_actions = [
                "retrieve_memories", "query_theory", "query_literature",
                "query_parallel", "generate_formulation", "refine_formulation", "finish"
            ]

            if thought.get("action") not in valid_actions:
                logger.warning(f"Invalid action '{thought.get('action')}', defaulting to retrieve_memories")
                thought["action"] = "retrieve_memories"

            # Apply deterministic policy guards (budgets, anti-parallel default, stagnation).
            return self._apply_think_policy(thought, task, knowledge_state, iteration, max_iterations)

        except Exception as e:
            logger.error(f"Think phase failed: {e}")
            # Fallback: simple heuristic
            if not knowledge_state["memories_retrieved"]:
                return self._apply_think_policy(
                    {
                    "action": "retrieve_memories",
                    "reasoning": "Starting with memory retrieval (fallback decision)",
                    "information_gaps": ["All information"]
                    },
                    task,
                    knowledge_state,
                    iteration,
                    max_iterations,
                )
            elif not knowledge_state["theory_knowledge"] and not knowledge_state["literature_knowledge"]:
                # Prefer literature first (fast, empirical). Only use CoreRAG if necessary/available.
                if self.largerag:
                    base = {
                        "action": "query_literature",
                        "reasoning": "Need empirical recipes/conditions to ground the search (fallback decision)",
                        "information_gaps": ["Empirical recipes", "Room-temperature performance data"],
                    }
                elif self.corerag:
                    base = {
                        "action": "query_theory",
                        "reasoning": "Need mechanistic/design-rule guidance (fallback decision)",
                        "information_gaps": ["Mechanism", "Design rules"],
                    }
                else:
                    base = {
                        "action": "generate_formulation",
                        "reasoning": "Tools unavailable; generate best-effort formulation from parametric knowledge (fallback decision)",
                        "information_gaps": [],
                    }

                return self._apply_think_policy(base, task, knowledge_state, iteration, max_iterations)
            else:
                return self._apply_think_policy(
                    {
                    "action": "generate_formulation",
                    "reasoning": "Have sufficient information (fallback decision)",
                    "information_gaps": []
                    },
                    task,
                    knowledge_state,
                    iteration,
                    max_iterations,
                )

    def _act(self, action: str, task: Dict, knowledge_state: Dict, tool_calls: List) -> Dict:
        """
        ACT phase: Execute the chosen action.

        Args:
            action: Action to execute
            task: Task specification
            knowledge_state: Current knowledge state (will be updated in-place)
            tool_calls: List to append tool call records

        Returns:
            Dict with:
                - action: Action that was executed
                - success: Whether action succeeded
                - data: Retrieved/generated data
                - summary: Human-readable summary
        """
        logger.info(f"[ACT] Executing action: {action}")

        if action == "retrieve_memories":
            memories = self._retrieve_memories(task)
            knowledge_state["memories"] = memories
            knowledge_state["memories_retrieved"] = True
            return {
                "action": "retrieve_memories",
                "success": True,
                "data": memories,
                "summary": f"Retrieved {len(memories)} relevant memories from past experiences"
            }

        elif action == "query_theory":
            theory = self._query_corerag(task, knowledge_state)
            if theory:
                knowledge_state["theory_knowledge"].append(theory)  # Accumulate
                knowledge_state["num_theory_queries"] += 1
                tool_calls.append({
                    "tool": "CoreRAG",
                    # Record the *actual* query sent to CoreRAG (LLM-generated), not the task description.
                    "query": (theory.get("_query_text") if isinstance(theory, dict) else task["description"]),
                    "result": theory
                })
            else:
                knowledge_state["failed_theory_attempts"] += 1  # Track failure
            return {
                "action": "query_theory",
                "success": theory is not None,
                "data": theory,
                "summary": f"Retrieved theoretical knowledge from CoreRAG ontology (query #{knowledge_state['num_theory_queries']})" if theory else "CoreRAG query failed"
            }

        elif action == "query_literature":
            literature = self._query_largerag(task, knowledge_state)
            if literature:
                knowledge_state["literature_knowledge"].append(literature)  # Accumulate
                knowledge_state["num_literature_queries"] += 1
                tool_calls.append({
                    "tool": "LargeRAG",
                    # Record the *actual* query sent to LargeRAG (LLM-generated), not the task description.
                    "query": (literature.get("_query_text") if isinstance(literature, dict) else task["description"]),
                    "result": literature
                })
            else:
                knowledge_state["failed_literature_attempts"] += 1  # Track failure
            return {
                "action": "query_literature",
                "success": literature is not None,
                "data": literature,
                "summary": f"Retrieved literature precedents from LargeRAG (query #{knowledge_state['num_literature_queries']})" if literature else "LargeRAG query failed"
            }

        elif action == "query_parallel":
            # Parallel query both tools
            knowledge_state["num_parallel_queries"] = knowledge_state.get("num_parallel_queries", 0) + 1
            theory, literature = self._query_tools_parallel(task, knowledge_state)

            if theory:
                knowledge_state["theory_knowledge"].append(theory)  # Accumulate
                knowledge_state["num_theory_queries"] += 1
                tool_calls.append({
                    "tool": "CoreRAG",
                    "query": (theory.get("_query_text") if isinstance(theory, dict) else task["description"]),
                    "result": theory
                })
            else:
                knowledge_state["failed_theory_attempts"] += 1

            if literature:
                knowledge_state["literature_knowledge"].append(literature)  # Accumulate
                knowledge_state["num_literature_queries"] += 1
                tool_calls.append({
                    "tool": "LargeRAG",
                    "query": (literature.get("_query_text") if isinstance(literature, dict) else task["description"]),
                    "result": literature
                })
            else:
                knowledge_state["failed_literature_attempts"] += 1

            return {
                "action": "query_parallel",
                "success": (theory is not None) or (literature is not None),
                "data": {"theory": theory, "literature": literature},
                "summary": f"Parallel query: CoreRAG {'✓ (query #' + str(knowledge_state['num_theory_queries']) + ')' if theory else '✗'}, LargeRAG {'✓ (query #' + str(knowledge_state['num_literature_queries']) + ')' if literature else '✗'}"
            }

        elif action == "generate_formulation":
            # Ensure memories are retrieved
            if not knowledge_state["memories_retrieved"]:
                knowledge_state["memories"] = self._retrieve_memories(task)
                knowledge_state["memories_retrieved"] = True

            formulation = self._generate_formulation(
                task,
                knowledge_state["memories"] or [],
                knowledge_state["theory_knowledge"],
                knowledge_state["literature_knowledge"]
            )

            knowledge_state["formulation_candidates"].append(formulation)
            diag = formulation.get("_diagnostics") if isinstance(formulation, dict) else None
            valid = bool(diag.get("validation_ok")) if isinstance(diag, dict) else False
            formulation_summary = summarize_formulation(
                (formulation or {}).get("formulation") if isinstance(formulation, dict) else None
            )
            return {
                "action": "generate_formulation",
                "success": valid,
                "data": formulation,
                "summary": (
                    f"Generated formulation: {formulation_summary} "
                    f"(confidence: {(formulation or {}).get('confidence', 0):.2f}, valid={valid})"
                ),
            }

        elif action == "refine_formulation":
            # Generate additional candidate with current knowledge
            if not knowledge_state["formulation_candidates"]:
                # No formulation to refine, generate new one
                return self._act("generate_formulation", task, knowledge_state, tool_calls)

            formulation = self._generate_formulation(
                task,
                knowledge_state["memories"] or [],
                knowledge_state["theory_knowledge"],
                knowledge_state["literature_knowledge"]
            )

            knowledge_state["formulation_candidates"].append(formulation)
            diag = formulation.get("_diagnostics") if isinstance(formulation, dict) else None
            valid = bool(diag.get("validation_ok")) if isinstance(diag, dict) else False
            formulation_summary = summarize_formulation(
                (formulation or {}).get("formulation") if isinstance(formulation, dict) else None
            )
            return {
                "action": "refine_formulation",
                "success": valid,
                "data": formulation,
                "summary": (
                    f"Refined formulation: {formulation_summary} "
                    f"(now have {len(knowledge_state['formulation_candidates'])} candidates, valid={valid})"
                ),
            }

        else:
            logger.warning(f"Unknown action: {action}")
            return {
                "action": action,
                "success": False,
                "data": None,
                "summary": f"Unknown action: {action}"
            }

    def _observe(self, action_result: Dict, knowledge_state: Dict, task: Dict, iteration: int) -> Dict:
        """
        OBSERVE phase: LLM-based analysis of action results.

        NEW: Uses LLM to analyze action results, extract insights, identify gaps,
        and recommend next actions. Replaces hardcoded logic with intelligent analysis.

        Args:
            action_result: Result from _act method
            knowledge_state: Current knowledge state (for context)
            task: Task specification
            iteration: Current iteration number

        Returns:
            Dict with:
                - action: Action that was executed
                - success: Whether action succeeded
                - summary: LLM-generated observation summary
                - knowledge_updated: List of updated knowledge domains
                - key_insights: List of extracted insights (NEW)
                - information_gaps: List of identified gaps (NEW)
                - information_sufficient: Whether we have enough info
                - recommended_next_action: LLM's recommendation (NEW)
                - recommendation_reasoning: Reasoning for recommendation (NEW)
        """
        # Calculate progress context
        max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
        progress_pct = int((iteration / max_iterations) * 100)

        if progress_pct < 40:
            stage = "Early"
        elif progress_pct < 75:
            stage = "Mid"
        else:
            stage = "Late"

        # Format action result details
        action_result_summary = format_action_result_for_observe(
            action_result["action"],
            action_result,
            knowledge_state
        )

        # Format recent observations
        recent_observations = self._format_observations(
            knowledge_state["observations"][-2:] if len(knowledge_state["observations"]) > 0 else []
        )

        # Build OBSERVE prompt
        observe_prompt = OBSERVE_PROMPT.format(
            task_description=task.get("description", ""),
            target_material=task.get("target_material", ""),
            target_temperature=task.get("target_temperature", 25),
            iteration=iteration,
            max_iterations=max_iterations,
            progress_pct=progress_pct,
            stage=stage,
            action=action_result["action"],
            success=action_result["success"],
            action_result_summary=action_result_summary,
            has_memories=knowledge_state["memories_retrieved"],
            num_memories=len(knowledge_state["memories"] or []),
            num_theory=knowledge_state["num_theory_queries"],
            failed_theory=knowledge_state["failed_theory_attempts"],
            num_literature=knowledge_state["num_literature_queries"],
            failed_literature=knowledge_state["failed_literature_attempts"],
            num_formulations=len(knowledge_state["formulation_candidates"]),
            num_observations=len(knowledge_state["observations"]),
            recent_observations=recent_observations
        )

        # Call LLM for observation analysis
        try:
            llm_output = self.llm_client(observe_prompt)
            observation = parse_observe_output(llm_output)

            # Add metadata
            observation["action"] = action_result["action"]
            observation["success"] = action_result["success"]

            # Normalize optional boolean fields (LLM may omit or mis-type them).
            if not isinstance(observation.get("information_sufficient"), bool):
                observation["information_sufficient"] = False
            if not isinstance(observation.get("stagnation_detected"), bool):
                observation["stagnation_detected"] = False

            logger.info(f"[OBSERVE] LLM analysis: {observation.get('summary', 'No summary')[:100]}...")

        except Exception as e:
            logger.error(f"OBSERVE LLM call failed: {e}, using fallback")
            # Fallback: minimal observation
            observation = {
                "action": action_result["action"],
                "success": action_result["success"],
                "summary": f"Action {action_result['action']} completed with status: {action_result['success']}",
                "knowledge_updated": [],
                "key_insights": [],
                "information_gaps": ["LLM observation failed"],
                "information_sufficient": False,
                "stagnation_detected": False,
                "recommended_next_action": "generate_formulation",
                "recommendation_reasoning": "Fallback due to LLM error"
            }

        return observation

    # ===== Helper Methods =====

    def _format_observations(self, observations: List[Dict]) -> str:
        """
        Format recent observations for display in prompts.

        NEW: Includes key_insights and information_gaps from LLM analysis.
        """
        if not observations:
            return "(No observations yet)"

        formatted = []
        for i, obs in enumerate(observations, 1):
            obs_text = f"{i}. **Summary**: {obs.get('summary', 'No summary')}"

            # Add key insights if available
            if obs.get("key_insights"):
                insights = obs["key_insights"][:2]  # Show max 2 insights
                insights_text = "; ".join(insights)
                obs_text += f"\n   **Insights**: {insights_text}"

            # Add identified gaps if available
            if obs.get("information_gaps"):
                gaps = obs["information_gaps"][:2]  # Show max 2 gaps
                gaps_text = "; ".join(gaps)
                obs_text += f"\n   **Gaps**: {gaps_text}"

            formatted.append(obs_text)

        return "\n".join(formatted)

    def _format_latest_observe_recommendation(self, observations: List[Dict]) -> str:
        """
        Format the latest OBSERVE phase recommendation for THINK prompt.

        NEW: Shows the LLM-generated recommendation from previous iteration.
        """
        if not observations or len(observations) == 0:
            return "(No previous observations - this is iteration 1)"

        latest = observations[-1]

        # Extract recommendation if available
        recommended_action = latest.get("recommended_next_action", "N/A")
        recommendation_reasoning = latest.get("recommendation_reasoning", "No reasoning provided")

        formatted = f"- **Recommended Action**: {recommended_action}\n"
        formatted += f"- **Reasoning**: {recommendation_reasoning}\n"
        formatted += f"- **Note**: You may follow this recommendation or choose a different action based on the full context."

        return formatted

    # ===== P2: Action policy helpers (query budgets + anti-parallel default) =====

    @staticmethod
    def _normalize_text_list(items: Any) -> List[str]:
        """
        Normalize a list of free-form strings for simple similarity checks.

        This is intentionally lightweight: it lets us detect obvious stagnation
        (e.g., identical information_gaps across consecutive iterations).
        """
        if not isinstance(items, list):
            return []
        out: List[str] = []
        for x in items:
            if x is None:
                continue
            s = str(x).strip().lower()
            if not s:
                continue
            s = re.sub(r"\s+", " ", s)
            out.append(s)
        return out

    def _is_stagnating(self, observations: List[Dict]) -> bool:
        """
        Return True when the agent is likely looping without new information.

        Heuristic (cheap + robust):
        - If the last two iterations report the same normalized information_gaps,
          assume additional broad queries are not adding value.
        """
        if not observations:
            return False

        # Prefer the LLM's explicit stagnation judgment when available.
        last = observations[-1] if isinstance(observations[-1], dict) else {}
        if isinstance(last, dict) and isinstance(last.get("stagnation_detected"), bool):
            return bool(last.get("stagnation_detected"))

        if len(observations) < 2:
            return False

        prev = observations[-2] if isinstance(observations[-2], dict) else {}

        gaps_last = set(self._normalize_text_list(last.get("information_gaps")))
        gaps_prev = set(self._normalize_text_list(prev.get("information_gaps")))

        if gaps_last and gaps_last == gaps_prev:
            return True

        return False

    def _count_valid_candidates(self, candidates: List[Any], expected_num_components: int) -> int:
        """Count schema-valid formulation candidates currently in the knowledge_state."""
        n = 0
        for cand in candidates or []:
            if not isinstance(cand, dict):
                continue
            f_norm = normalize_formulation(cand.get("formulation"), expected_num_components)
            ok_f, _ = validate_formulation(
                f_norm, expected_num_components, require_functions=True
            )
            if ok_f:
                n += 1
        return n

    def _apply_think_policy(
        self,
        thought: Dict[str, Any],
        task: Dict,
        knowledge_state: Dict,
        iteration: int,
        max_iterations: int,
    ) -> Dict[str, Any]:
        """
        Deterministic policy layer on top of LLM planning.

        Goals:
        - Prevent default overuse of query_parallel (especially CoreRAG load).
        - Enforce basic budgets (configurable) for CoreRAG/LargeRAG/parallel.
        - Encourage earlier formulation once sufficient knowledge exists.
        - Stop early when we already have a valid, testable formulation.
        """
        if not isinstance(thought, dict):
            return {"action": "generate_formulation", "reasoning": "Invalid thought object", "information_gaps": []}

        thought.setdefault("action", "generate_formulation")
        thought.setdefault("reasoning", "")
        if not isinstance(thought.get("information_gaps"), list):
            thought["information_gaps"] = []

        agent_cfg = self.config.get("agent", {}) or {}
        max_corerag = int(agent_cfg.get("max_corerag_queries", 3))
        max_largerag = int(agent_cfg.get("max_largerag_queries", 8))
        max_parallel = int(agent_cfg.get("max_parallel_queries", 3))

        expected_num_components = int(task.get("num_components") or 2)
        num_valid = self._count_valid_candidates(
            knowledge_state.get("formulation_candidates") or [],
            expected_num_components,
        )

        remaining = max_iterations - iteration
        stagnating = self._is_stagnating(knowledge_state.get("observations") or [])

        num_theory = int(knowledge_state.get("num_theory_queries", 0))
        num_lit = int(knowledge_state.get("num_literature_queries", 0))
        num_parallel = int(knowledge_state.get("num_parallel_queries", 0))
        num_candidates = len(knowledge_state.get("formulation_candidates") or [])

        last_obs = None
        if knowledge_state.get("observations"):
            maybe = (knowledge_state.get("observations") or [])[-1]
            last_obs = maybe if isinstance(maybe, dict) else None

        info_sufficient = bool(last_obs.get("information_sufficient")) if isinstance(last_obs, dict) else False

        # 1) If we already have a valid candidate and info is sufficient -> finish now.
        if num_valid > 0 and info_sufficient:
            thought["action"] = "finish"
            thought["reasoning"] = (
                "Policy: information is sufficient and a schema-valid formulation exists; finishing early.\n"
                + str(thought.get("reasoning") or "").strip()
            ).strip()
            return thought

        original_action = str(thought.get("action") or "").strip()
        action = original_action
        override_reason: Optional[str] = None

        # 2) Enforce query budgets (CoreRAG/LargeRAG/parallel).
        if action == "query_theory" and num_theory >= max_corerag:
            action = "query_literature" if self.largerag and num_lit < max_largerag else "generate_formulation"
            override_reason = f"CoreRAG budget reached (>= {max_corerag}); avoiding additional theory queries."

        if action == "query_literature" and num_lit >= max_largerag:
            action = "generate_formulation"
            override_reason = f"LargeRAG budget reached (>= {max_largerag}); moving to formulation."

        if action == "query_parallel":
            # query_parallel is never the default; allow only when BOTH are missing and budget allows.
            has_theory = num_theory > 0
            has_lit = num_lit > 0

            if num_parallel >= max_parallel:
                action = "query_literature" if self.largerag and num_lit < max_largerag else "generate_formulation"
                override_reason = f"query_parallel budget reached (>= {max_parallel}); avoid running both tools again."
            elif has_theory and not has_lit:
                action = "query_literature" if self.largerag else "generate_formulation"
                override_reason = "Already have theory; query literature only (avoid unnecessary CoreRAG)."
            elif has_lit and not has_theory:
                action = "query_theory" if self.corerag and num_theory < max_corerag else "generate_formulation"
                override_reason = "Already have literature; query theory only if budget allows."
            elif has_theory and has_lit:
                # When both exist, parallel adds cost but rarely adds decisive value.
                action = "generate_formulation" if num_candidates == 0 else "refine_formulation"
                override_reason = "Already have theory + literature; move to formulation instead of parallel querying."
            else:
                # Both missing: allow at most once (budgeted) early in the run.
                pass

        # 3) Stagnation: if gaps repeat, stop searching and write/refine.
        if stagnating and action in ("query_theory", "query_literature", "query_parallel"):
            if num_candidates == 0:
                action = "generate_formulation"
                override_reason = "Stagnation detected (repeating information gaps); stop querying and generate a candidate."
            elif num_valid > 0:
                action = "finish"
                override_reason = "Stagnation detected and a valid candidate exists; finishing early."
            else:
                action = "refine_formulation"
                override_reason = "Stagnation detected; refine candidates instead of more searching."

        # 4) Encourage earlier formulation once sufficient sources exist.
        # If we already have both theory+literature and still no candidates, don't keep querying.
        if (
            action in ("query_theory", "query_literature", "query_parallel")
            and num_candidates == 0
            and num_theory > 0
            and num_lit > 0
            and iteration >= max(4, int(0.3 * max_iterations))
        ):
            action = "generate_formulation"
            override_reason = "Already have theory + literature; generate formulation instead of additional queries."

        # 5) Final rounds: prioritize producing/finishing.
        if remaining <= 2 and action in ("query_theory", "query_parallel"):
            # CoreRAG is slow; late-stage parallel/theory is rarely worth it.
            action = "generate_formulation" if num_candidates == 0 else "refine_formulation"
            override_reason = "Final rounds policy: avoid slow theory/parallel queries; focus on formulation output."

        # Ensure tools exist for the chosen query action.
        if action == "query_theory" and not self.corerag:
            action = "query_literature" if self.largerag else "generate_formulation"
            override_reason = "CoreRAG unavailable; switching action."
        if action == "query_literature" and not self.largerag:
            action = "query_theory" if self.corerag and num_theory < max_corerag else "generate_formulation"
            override_reason = "LargeRAG unavailable; switching action."
        if action == "query_parallel" and not (self.corerag or self.largerag):
            action = "generate_formulation"
            override_reason = "No tools available for query_parallel; switching to formulation."

        # Apply override
        if action != original_action:
            thought["action"] = action
            prefix = f"Policy override: {override_reason}" if override_reason else "Policy override applied."
            thought["reasoning"] = (prefix + "\n" + str(thought.get("reasoning") or "").strip()).strip()

        return thought

    def _parse_json_response(self, llm_output: str) -> Dict:
        """Parse JSON from LLM response with multiple fallback strategies."""
        # Try to extract JSON block
        json_match = re.search(r'```json\s*(.*?)\s*```', llm_output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find any JSON object
        json_match = re.search(r'\{.*?\}', llm_output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Fallback: return empty dict
        logger.warning("Could not parse JSON from LLM output")
        return {}

    def _query_tools_parallel(self, task: Dict, knowledge_state: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        Query CoreRAG and LargeRAG in parallel for efficiency.

        Returns:
            (theory_knowledge, literature_knowledge)
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._query_tools_parallel_async(task, knowledge_state))
            loop.close()
            return result
        except Exception as e:
            logger.error(f"Parallel query failed: {e}")
            # Fallback to sequential
            agent_cfg = self.config.get("agent", {}) or {}
            max_corerag = int(agent_cfg.get("max_corerag_queries", 3))
            max_largerag = int(agent_cfg.get("max_largerag_queries", 8))

            theory = None
            literature = None
            if self.corerag and int(knowledge_state.get("num_theory_queries", 0)) < max_corerag:
                theory = self._query_corerag(task, knowledge_state)
            if self.largerag and int(knowledge_state.get("num_literature_queries", 0)) < max_largerag:
                literature = self._query_largerag(task, knowledge_state)
            return theory, literature

    async def _query_tools_parallel_async(self, task: Dict, knowledge_state: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Async version of parallel tool query."""
        loop = asyncio.get_event_loop()

        # Helper coroutine to return None when tool is unavailable
        async def return_none():
            return None

        # Policy guard: CoreRAG is expensive. If budgets are exceeded, skip the tool
        # even when query_parallel is selected (prevents runaway CoreRAG calls).
        agent_cfg = self.config.get("agent", {}) or {}
        max_corerag = int(agent_cfg.get("max_corerag_queries", 3))
        max_largerag = int(agent_cfg.get("max_largerag_queries", 8))
        allow_corerag = bool(self.corerag) and int(knowledge_state.get("num_theory_queries", 0)) < max_corerag
        allow_largerag = bool(self.largerag) and int(knowledge_state.get("num_literature_queries", 0)) < max_largerag

        # Create tasks
        tasks = []
        if allow_corerag:
            tasks.append(loop.run_in_executor(None, self._query_corerag, task, knowledge_state))
        else:
            tasks.append(return_none())

        if allow_largerag:
            tasks.append(loop.run_in_executor(None, self._query_largerag, task, knowledge_state))
        else:
            tasks.append(return_none())

        # Execute in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle results
        theory = results[0] if not isinstance(results[0], Exception) else None
        literature = results[1] if not isinstance(results[1], Exception) else None

        return theory, literature

    def solve_task(self, task: Dict) -> Dict:
        """
        Main entry point for solving a DES formulation task using ReAct loop.

        NEW: ReAct (Reasoning-Acting) paradigm:
        - Think: Analyze current knowledge state and decide next action
        - Act: Execute the chosen action (query tools, generate formulation)
        - Observe: Summarize results and update knowledge state

        This creates a cumulative information-building process similar to deep research agents.

        Args:
            task: Task dictionary with keys:
                - task_id: Unique identifier
                - description: Natural language description
                - target_material: Material to dissolve
                - target_temperature: Target temperature (°C)
                - constraints: Additional constraints

        Returns:
            Dict with keys:
                - formulation: Proposed DES formulation
                - reasoning: Explanation of design choices
                - confidence: Confidence score (0-1)
                - supporting_evidence: Literature/theory references
                - status: "PENDING"
                - iterations_used: Number of ReAct iterations
        """
        task_id = task.get("task_id", f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        logger.info(f"[ReAct Agent] Starting task {task_id}: {task['description'][:50]}...")

        # Initialize knowledge state
        knowledge_state = {
            "memories": None,
            "memories_retrieved": False,
            "theory_knowledge": [],  # Changed: List to accumulate all theory queries
            "literature_knowledge": [],  # Changed: List to accumulate all literature queries
            "formulation_candidates": [],
            "observations": [],
            "information_gaps": [],  # Track what we still need to know
            "num_theory_queries": 0,  # Track number of CoreRAG queries
            "num_literature_queries": 0,  # Track number of LargeRAG queries
            "num_parallel_queries": 0,  # Track number of query_parallel actions executed
            "failed_theory_attempts": 0,  # NEW: Track failed CoreRAG attempts
            "failed_literature_attempts": 0,  # NEW: Track failed LargeRAG attempts
        }

        # Initialize trajectory tracking
        trajectory_steps = []
        tool_calls = []

        # ReAct loop parameters
        max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
        iteration = 0
        task_complete = False

        # ===== ReAct Loop =====
        while iteration < max_iterations and not task_complete:
            iteration += 1
            logger.info(f"\n{'='*60}")
            logger.info(f"[ReAct Iteration {iteration}/{max_iterations}]")
            logger.info(f"{'='*60}")

            # THINK: Decide next action based on current knowledge state
            thought = self._think(task, knowledge_state, iteration)
            logger.info(f"[THINK] {thought['reasoning']}")
            logger.info(f"[THINK] Next action: {thought['action']}")

            trajectory_steps.append({
                "iteration": iteration,
                "phase": "think",
                "reasoning": thought["reasoning"],
                "action": thought["action"],
                "information_gaps": thought.get("information_gaps", [])
            })

            # Check if ready to finish
            if thought["action"] == "finish":
                logger.info("[THINK] Decision: Task complete, ready to finalize")
                task_complete = True
                break

            # ACT: Execute the chosen action
            action_result = self._act(thought["action"], task, knowledge_state, tool_calls)
            logger.info(f"[ACT] Executed: {thought['action']}")

            trajectory_steps.append({
                "iteration": iteration,
                "phase": "act",
                "action": thought["action"],
                "result_summary": action_result.get("summary", "")
            })

            # OBSERVE: Summarize and integrate new information
            observation = self._observe(action_result, knowledge_state, task, iteration)
            logger.info(f"[OBSERVE] {observation['summary']}")

            knowledge_state["observations"].append(observation)

            # NEW: Update information_gaps from observation
            if observation.get("information_gaps"):
                knowledge_state["information_gaps"] = observation["information_gaps"]
                logger.debug(f"[OBSERVE] Updated information gaps: {observation['information_gaps']}")

            trajectory_steps.append({
                "iteration": iteration,
                "phase": "observe",
                "observation": observation["summary"],
                "knowledge_updated": observation.get("knowledge_updated", []),
                "key_insights": observation.get("key_insights", []),
                "information_gaps": observation.get("information_gaps", [])
            })

            # ===== P2: Early stopping (avoid "always run to max_iterations") =====
            #
            # If we already have at least one schema-valid formulation candidate and the
            # OBSERVE analysis says information is sufficient (or we are stagnating),
            # stop looping and finalize. This preserves research quality while avoiding
            # redundant tool calls (especially CoreRAG).
            if bool(self.config.get("agent", {}).get("allow_early_stopping", True)):
                expected_num_components = int(task.get("num_components") or 2)
                num_valid = self._count_valid_candidates(
                    knowledge_state.get("formulation_candidates") or [],
                    expected_num_components,
                )
                stagnating = self._is_stagnating(knowledge_state.get("observations") or [])
                remaining = max_iterations - iteration

                if num_valid > 0 and (
                    bool(observation.get("information_sufficient"))
                    or stagnating
                    or remaining <= 1
                ):
                    logger.info(
                        "[Policy] Early stopping: valid formulation exists and information is sufficient/stalled (valid=%s, suff=%s, stagnating=%s, remaining=%s).",
                        num_valid,
                        bool(observation.get("information_sufficient")),
                        stagnating,
                        remaining,
                    )
                    task_complete = True
                    break

        # ===== Finalize Formulation =====
        logger.info(f"\n[ReAct Agent] Finalizing after {iteration} iterations")

        expected_num_components = int(task.get("num_components") or 2)

        # If no formulation generated yet, generate now (and store as candidate #1)
        if not knowledge_state.get("formulation_candidates"):
            logger.info("[Final] Generating formulation from accumulated knowledge")
            if not knowledge_state["memories_retrieved"]:
                knowledge_state["memories"] = self._retrieve_memories(task)
                knowledge_state["memories_retrieved"] = True

            cand0 = self._generate_formulation(
                task,
                knowledge_state["memories"] or [],
                knowledge_state["theory_knowledge"],
                knowledge_state["literature_knowledge"]
            )
            knowledge_state["formulation_candidates"] = [cand0]

        # Select the best VALID candidate (do not blindly pick [0]).
        candidate_summaries: List[Dict[str, Any]] = []
        valid_candidates: List[Tuple[float, int, Dict[str, Any]]] = []

        for idx, cand in enumerate(knowledge_state["formulation_candidates"], start=1):
            if not isinstance(cand, dict):
                candidate_summaries.append(
                    {
                        "index": idx,
                        "valid": False,
                        "confidence": 0.0,
                        "summary": "<invalid candidate object>",
                        "errors": ["candidate is not a dict/object"],
                    }
                )
                continue

            f_norm = normalize_formulation(cand.get("formulation"), expected_num_components)
            ok_f, f_errors = validate_formulation(
                f_norm, expected_num_components, require_functions=True
            )

            try:
                conf = float(cand.get("confidence", 0.0) or 0.0)
            except Exception:
                conf = 0.0

            summary = summarize_formulation(f_norm)
            candidate_summaries.append(
                {
                    "index": idx,
                    "valid": ok_f,
                    "confidence": conf,
                    "summary": summary,
                    "errors": f_errors,
                }
            )

            if ok_f:
                valid_candidates.append((conf, idx, cand))

        selected_candidate_index: Optional[int] = None
        final_status = "FAILED"
        outcome = "failed_generation"

        if valid_candidates:
            # Prefer higher confidence; tie-breaker: later candidate (refinement) wins.
            _, selected_candidate_index, formulation_result = max(
                valid_candidates, key=lambda t: (t[0], t[1])
            )
            final_status = "PENDING"
            outcome = "pending_experiment"
        else:
            # No valid candidate: persist a FAILED recommendation with diagnostic reasoning.
            formulation_result = (
                knowledge_state["formulation_candidates"][-1]
                if knowledge_state.get("formulation_candidates")
                else {}
            )
            if not isinstance(formulation_result, dict):
                formulation_result = {}

            # Construct an informative failure message for the UI.
            fail_lines = [
                f"No valid {expected_num_components}-component formulation could be produced.",
                f"Candidates attempted: {len(knowledge_state.get('formulation_candidates') or [])}.",
            ]
            for meta in candidate_summaries[-3:]:
                # Show last few candidates for fast debugging.
                fail_lines.append(
                    f"- candidate#{meta.get('index')}: {meta.get('summary')} | "
                    f"confidence={meta.get('confidence')} | valid={meta.get('valid')} | "
                    f"errors={meta.get('errors')}"
                )

            formulation_result = dict(formulation_result)
            formulation_result.setdefault("formulation", {})
            formulation_result["confidence"] = 0.0
            formulation_result["supporting_evidence"] = []
            formulation_result["reasoning"] = "\n".join(fail_lines)

        # Add memories_used to formulation_result for trajectory persistence
        formulation_result["memories_used"] = [m.title for m in (knowledge_state["memories"] or [])]

        # ===== Create Trajectory Record =====
        trajectory = Trajectory(
            task_id=task_id,
            task_description=task["description"],
            steps=trajectory_steps,
            outcome=outcome,
            final_result=formulation_result,
            metadata={
                "target_material": task.get("target_material"),
                "target_temperature": task.get("target_temperature"),
                "constraints": task.get("constraints", {}),
                "tool_calls": tool_calls,
                "iterations_used": iteration,
                "react_mode": True,
                "expected_num_components": expected_num_components,
                "candidate_summaries": candidate_summaries,
                "selected_candidate_index": selected_candidate_index,
                "final_status": final_status,
                "final_knowledge_state": {
                    "had_memories": knowledge_state["memories_retrieved"],
                    "had_theory": len(knowledge_state["theory_knowledge"]) > 0,
                    "had_literature": len(knowledge_state["literature_knowledge"]) > 0,
                    "num_theory_queries": knowledge_state["num_theory_queries"],
                    "num_literature_queries": knowledge_state["num_literature_queries"],
                }
            }
        )

        # ===== Create Recommendation Record =====
        rec_id = f"REC_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{task_id}"

        recommendation = Recommendation(
            recommendation_id=rec_id,
            task=task,
            task_id=task_id,
            formulation=formulation_result.get("formulation", {}),
            reasoning=formulation_result.get("reasoning", ""),
            confidence=formulation_result.get("confidence", 0.0),
            trajectory=trajectory,
            status=final_status,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )

        self.rec_manager.save_recommendation(recommendation)
        logger.info(f"[ReAct Agent] Saved recommendation {rec_id}")

        # ===== Prepare Return Result =====
        result = formulation_result.copy()
        result["recommendation_id"] = rec_id
        result["status"] = final_status
        result["task_id"] = task_id
        result["iterations_used"] = iteration
        result["memories_used"] = [m.title for m in (knowledge_state["memories"] or [])]
        result["information_sources"] = {
            "memories": knowledge_state["memories_retrieved"],
            "theory": len(knowledge_state["theory_knowledge"]) > 0,
            "literature": len(knowledge_state["literature_knowledge"]) > 0
        }
        if final_status == "PENDING":
            result["next_steps"] = (
                f"Recommendation {rec_id} is ready for experimental testing. "
                f"Submit feedback using agent.submit_experiment_feedback('{rec_id}', experiment_result)."
            )
        else:
            result["next_steps"] = (
                f"Recommendation {rec_id} failed to generate a valid formulation. "
                f"Review the failure reasoning and retry generation (or adjust prompt/config)."
            )

        logger.info(f"[ReAct Agent] Task {task_id} completed in {iteration} iterations")
        return result

    def _retrieve_memories(self, task: Dict) -> List[MemoryItem]:
        """
        Retrieve relevant memories for the task.

        NEW: LLM decides how many memories to retrieve based on task complexity and memory availability.

        Args:
            task: Task dictionary

        Returns:
            List of relevant MemoryItem objects
        """
        # Get total number of memories in the bank
        total_memories = len(self.memory.get_all_memories())

        logger.info(f"[Memory Retrieval] Total memories in bank: {total_memories}")

        if total_memories == 0:
            logger.info("[Memory Retrieval] No memories available")
            return []

        # Let LLM decide how many memories to retrieve
        decision_prompt = f"""You are deciding how many memories to retrieve for a DES formulation task.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Target Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Memory Bank Status**:
- Total memories available: {total_memories}
- These memories contain validated experimental data from past DES formulations

**Your Decision**:
Decide how many memories to retrieve (top_k parameter) based on:
1. **Task complexity**: Simple binary DES → fewer memories (1-3); Complex multi-component DES → more memories (3-10)
2. **Available memories**: If total < 5, retrieve all; if total >= 5, be selective
3. **Material specificity**: Novel material → retrieve more for broader context; Common material → fewer targeted memories
4. **Temperature**: Non-standard temperature → retrieve more examples; Standard temperature → fewer examples

**Guidelines**:
- **Minimum**: 2 (always try to get at least two relevant memory)
- **Maximum**: min(10, total_memories) (don't overwhelm with too many)

Output ONLY a JSON object:
{{
    "top_k": <number>,
    "reasoning": "<1-2 sentences explaining your choice>"
}}
"""

        try:
            llm_response = self.llm_client(decision_prompt)
            decision = self._parse_json_response(llm_response)

            # Extract top_k with validation
            top_k = decision.get("top_k", 3)
            reasoning = decision.get("reasoning", "Default retrieval strategy")

            # Validate and constrain top_k
            top_k = max(1, min(top_k, total_memories, 10))

            logger.info(f"[Memory Retrieval] LLM decision: retrieve top_{top_k} memories")
            logger.info(f"[Memory Retrieval] Reasoning: {reasoning}")

        except Exception as e:
            logger.warning(f"[Memory Retrieval] LLM decision failed: {e}, using default top_k=3")
            top_k = min(3, total_memories)

        # Perform retrieval with LLM-decided top_k
        query = MemoryQuery(
            query_text=task["description"],
            top_k=top_k,
            min_similarity=self.config.get("memory", {}).get("min_similarity", 0.0)
        )

        memories = self.retriever.retrieve(query)
        logger.info(f"[Memory Retrieval] Retrieved {len(memories)} memories (requested: {top_k}, available: {total_memories})")

        return memories

    def _query_corerag(self, task: Dict, knowledge_state: Dict) -> Optional[Dict]:
        """
        Query CoreRAG for theoretical knowledge using LLM-generated query.

        Args:
            task: Task dictionary
            knowledge_state: Current knowledge state (to generate informed queries)

        Returns:
            Dict with theory knowledge, or None if unavailable
        """
        if not self.corerag:
            logger.warning("CoreRAG client not available")
            return None

        try:
            # Let LLM generate the query based on current knowledge state
            num_prev_queries = knowledge_state["num_theory_queries"]

            # Summarize what we already know
            prev_theory_summary = ""
            if knowledge_state["theory_knowledge"]:
                prev_theory_summary = f"\n**Previous theory queries ({num_prev_queries} total):**\n"
                for i, theory in enumerate(knowledge_state["theory_knowledge"][-2:], start=max(1, num_prev_queries-1)):
                    prev_theory_summary += f"Query {i}: Retrieved theoretical knowledge\n"

            literature_summary = ""
            if knowledge_state["literature_knowledge"]:
                literature_summary = f"\n**Literature knowledge acquired:** {len(knowledge_state['literature_knowledge'])} queries completed"

            query_gen_prompt = f"""You are generating a query for CoreRAG (theoretical ontology database) to support DES formulation design.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Current Knowledge State**:
- Theory queries completed: {num_prev_queries}
- Literature queries completed: {knowledge_state['num_literature_queries']}
{prev_theory_summary}
{literature_summary}

**Your Goal**: Generate a comprehensive, well-structured CoreRAG query to retrieve theoretical knowledge.

**Guidelines**:
- If this is the FIRST theory query (query #{num_prev_queries + 1}): Ask for comprehensive theoretical foundations (hydrogen bonding, component selection, molar ratios, temperature effects)
- If this is a SUBSEQUENT query: Ask for complementary theoretical insights not covered in previous queries
- Be specific and detailed to maximize information gain
- CoreRAG takes 5-10 minutes per query, so make it count!

Output ONLY the query text (no JSON, no explanation):"""

            query_text = self.llm_client(query_gen_prompt).strip()
            # Remove quotes if LLM added them
            query_text = query_text.strip('"').strip("'")

            logger.info(f"[CoreRAG Query #{num_prev_queries + 1}] LLM generated: {query_text[:100]}...")

            # Format query for CoreRAG
            query = {
                "query": query_text,
                "focus": ["hydrogen_bonding", "component_selection", "molar_ratio", "temperature_effects"]
            }

            # Call CoreRAG
            result = self.corerag.query(query)
            logger.debug(f"CoreRAG returned: {str(result)[:100]}...")

            # Preserve traceability: keep the actual query used alongside the result.
            if isinstance(result, dict):
                result.setdefault("_query_text", query_text)
                result.setdefault("_query_payload", query)
                return result

            # Defensive fallback: always return a dict-like structure upstream can log.
            return {
                "_query_text": query_text,
                "_query_payload": query,
                "raw_result": result,
            }

        except Exception as e:
            logger.error(f"CoreRAG query failed: {e}")
            return None

    def _query_largerag(self, task: Dict, knowledge_state: Dict) -> Optional[Dict]:
        """
        Query LargeRAG for literature precedents using LLM-generated query.

        Args:
            task: Task dictionary
            knowledge_state: Current knowledge state (to generate diverse queries)

        Returns:
            Dict with literature knowledge, or None if unavailable
        """
        if not self.largerag:
            logger.warning("LargeRAG client not available")
            return None

        try:
            num_prev_queries = knowledge_state["num_literature_queries"]

            # Summarize previous queries to avoid repetition
            prev_lit_summary = ""
            if knowledge_state["literature_knowledge"]:
                prev_lit_summary = f"\n**Previous literature queries ({num_prev_queries} total):**\n"
                prev_lit_summary += f"Already retrieved {num_prev_queries * 10} documents from literature.\n"
                prev_lit_summary += "Generate a DIFFERENT query to explore new angles (e.g., different keywords, component variations, property focus)."

            theory_summary = ""
            if knowledge_state["theory_knowledge"]:
                theory_summary = f"\n**Theoretical knowledge available:** {len(knowledge_state['theory_knowledge'])} theory queries completed"

            query_gen_prompt = f"""You are generating a query for LargeRAG (literature database with 10,000+ papers) to support DES formulation design.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Current Knowledge State**:
- Literature queries completed: {num_prev_queries}
- Theory queries completed: {knowledge_state['num_theory_queries']}
{prev_lit_summary}
{theory_summary}

**Your Goal**: Generate a literature search query to find relevant DES formulations and experimental data.

**Guidelines**:
- Query #{num_prev_queries + 1} of literature search
- If first query: Search for direct DES formulation examples
- If subsequent query: Explore DIFFERENT angles (e.g., component variations, property data, dissolution mechanisms, alternative formulations)
- **IMPORTANT**: Make each query DIFFERENT from previous ones to maximize information coverage
- Use specific keywords relevant to DES and {task['target_material']}

Output ONLY the query text (no JSON, no explanation):"""

            query_text = self.llm_client(query_gen_prompt).strip()
            # Remove quotes if LLM added them
            query_text = query_text.strip('"').strip("'")

            logger.info(f"[LargeRAG Query #{num_prev_queries + 1}] LLM generated: {query_text[:100]}...")

            # Format query for LargeRAG
            query = {
                "query": query_text,
                "filters": {
                    "material_type": task.get("material_category", "polymer"),
                    "temperature_range": [task.get("target_temperature", 25) - 10, task.get("target_temperature", 25) + 10]
                },
                "top_k": self.config.get("tools", {}).get("largerag", {}).get("max_results", 10)
            }

            # Call LargeRAG
            result = self.largerag.query(query)
            logger.debug(f"LargeRAG returned: {str(result)[:100]}...")

            # Preserve traceability: keep the actual query used alongside the result.
            if isinstance(result, dict):
                result.setdefault("_query_text", query_text)
                result.setdefault("_query_payload", query)
                return result

            return {
                "_query_text": query_text,
                "_query_payload": query,
                "raw_result": result,
            }

        except Exception as e:
            logger.error(f"LargeRAG query failed: {e}")
            return None

    def _generate_formulation(
        self,
        task: Dict,
        memories: List[MemoryItem],
        theory_list: List[Dict],  # Changed: Now a list of all theory queries
        literature_list: List[Dict]  # Changed: Now a list of all literature queries
    ) -> Dict:
        """
        Generate DES formulation using LLM with all available knowledge.

        Args:
            task: Task dictionary
            memories: Retrieved memory items
            theory_list: List of all CoreRAG theory knowledge retrieved
            literature_list: List of all LargeRAG literature knowledge retrieved

        Returns:
            Dict with formulation, reasoning, confidence, etc.
        """
        # Build comprehensive prompt
        prompt = self._build_formulation_prompt(task, memories, theory_list, literature_list)

        expected_num_components = int(task.get("num_components") or 2)

        # Prefer OpenAI's official structured outputs when available.
        # Priority:
        # 1) Function calling with strict schema (best for schema compliance)
        # 2) Chat Completions response_format=json_schema strict (fallback)
        tool_spec: Optional[Tuple[List[Dict[str, Any]], Any, str]] = None
        response_format = None
        if getattr(self.llm_client, "provider", None) == "openai":
            tool_spec = self._build_formulation_tool_spec(expected_num_components)
            response_format = self._build_formulation_response_format(expected_num_components)
            if tool_spec is not None:
                logger.info(
                    "[Formulation] Using OpenAI function calling strict for structured output "
                    "(num_components=%s).",
                    expected_num_components,
                )
            elif response_format is not None:
                logger.info(
                    "[Formulation] Using OpenAI response_format=json_schema strict for structured output "
                    "(num_components=%s).",
                    expected_num_components,
                )

        diagnostics = {
            "expected_num_components": expected_num_components,
            "requested_tool_call": bool(tool_spec),
            "used_tool_call": False,
            "tool_name": (tool_spec[2] if tool_spec is not None else None),
            "requested_response_format": bool(response_format),
            "used_response_format": False,
            "strategy_used": None,
            # Two-stage structured output: draft (reasoning) -> strict JSON (no reasoning)
            "draft_used": False,
            "draft_output": "",
            "repair_used": False,
            "parse_ok": False,
            "validation_ok": False,
            # Fatal validation errors (blocks persistence as PENDING)
            "validation_errors": [],
            # Non-fatal normalization notes (kept for debugging)
            "notes": [],
            # Keep raw outputs for debugging; do NOT delete tool objects elsewhere.
            "raw_outputs": [],
            "raw_tool_calls": [],
        }

        def _parse_tool_call_json(tool_calls: Any, fn_name: str) -> Optional[Dict[str, Any]]:
            """
            Extract JSON object from tool call arguments for the expected function.

            We expect a single function call. If multiple are present, prefer
            the first matching name.
            """
            if not tool_calls or not isinstance(tool_calls, list):
                return None
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if not isinstance(fn, dict):
                    continue
                if fn.get("name") != fn_name:
                    continue
                args = fn.get("arguments")
                if isinstance(args, dict):
                    return dict(args)
                if isinstance(args, str) and args.strip():
                    try:
                        parsed = json.loads(args)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        # Fall back to best-effort JSON extraction.
                        parsed = loads_json_from_text(args)
                        if isinstance(parsed, dict):
                            return parsed
            return None

        def _attempt(
            llm_prompt: str,
            *,
            strategy: str,
            reasoning_effort: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
        ) -> Dict:
            """One LLM attempt + parse/normalize/validate."""
            try:
                diagnostics["strategy_used"] = strategy

                if strategy == "tool_call" and tool_spec is not None:
                    tools, tool_choice, fn_name = tool_spec
                    # NOTE: We must call .chat() directly to receive tool_calls.
                    resp = self.llm_client.chat(  # type: ignore[attr-defined]
                        llm_prompt,
                        tools=tools,
                        tool_choice=tool_choice,
                        parallel_tool_calls=False,
                        return_tool_calls=True,
                        reasoning_effort=reasoning_effort,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    llm_output = str((resp or {}).get("content") or "")
                    tool_calls = (resp or {}).get("tool_calls")
                    diagnostics["raw_outputs"].append(llm_output)
                    diagnostics["raw_tool_calls"].append(tool_calls)
                    parsed = _parse_tool_call_json(tool_calls, fn_name)
                    if parsed is None:
                        return {
                            "candidate": None,
                            "fatal_errors": [
                                "Missing/invalid tool call arguments (structured output not produced)"
                            ],
                            "notes": [],
                        }
                    diagnostics["used_tool_call"] = True
                elif strategy == "response_format" and response_format is not None:
                    llm_output = self.llm_client(
                        llm_prompt,
                        response_format=response_format,
                        reasoning_effort=reasoning_effort,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    llm_output = llm_output or ""
                    diagnostics["raw_outputs"].append(llm_output)
                    diagnostics["used_response_format"] = True
                    parsed = loads_json_from_text(llm_output)
                else:
                    llm_output = self.llm_client(
                        llm_prompt,
                        reasoning_effort=reasoning_effort,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    llm_output = llm_output or ""
                    diagnostics["raw_outputs"].append(llm_output)
                    parsed = loads_json_from_text(llm_output)

                logger.debug(f"LLM formulation output: {llm_output[:200]}...")
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                return {
                    "candidate": None,
                    "fatal_errors": [f"LLM call failed: {str(e)}"],
                    "notes": [],
                }

            if not isinstance(parsed, dict):
                return {
                    "candidate": None,
                    "fatal_errors": ["Could not parse a JSON object from LLM output"],
                    "notes": [],
                }

            cand: Dict = dict(parsed)
            cand.setdefault("formulation", {})
            cand.setdefault("reasoning", "")
            cand.setdefault("confidence", 0.0)
            cand.setdefault("supporting_evidence", [])

            # Normalize types for downstream UI/persistence.
            cand["formulation"] = normalize_formulation(
                cand.get("formulation"), expected_num_components
            )
            if expected_num_components != 2:
                cand.setdefault("synergy_explanation", "")

            fatal_errors: List[str] = []
            notes: List[str] = []

            # Validate formulation structure (fixed component count).
            ok_form, form_errors = validate_formulation(
                cand["formulation"], expected_num_components, require_functions=True
            )
            if not ok_form:
                fatal_errors.extend(form_errors)

            # Validate confidence
            try:
                cand["confidence"] = float(cand.get("confidence", 0.0))
            except Exception:
                cand["confidence"] = 0.0
                notes.append("confidence must be a number (normalized to 0.0)")
            if not (0.0 <= float(cand["confidence"]) <= 1.0):
                notes.append("confidence must be between 0.0 and 1.0 (clamped)")
                cand["confidence"] = max(0.0, min(1.0, float(cand["confidence"])))

            # Validate reasoning (research-grade: don't allow empty)
            if not isinstance(cand.get("reasoning"), str) or not cand["reasoning"].strip():
                fatal_errors.append("missing/empty reasoning")
                cand["reasoning"] = str(cand.get("reasoning") or "").strip()

            # Multi-component: require synergy explanation
            if expected_num_components != 2:
                if (
                    not isinstance(cand.get("synergy_explanation"), str)
                    or not cand["synergy_explanation"].strip()
                ):
                    fatal_errors.append("missing/empty synergy_explanation")
                    cand["synergy_explanation"] = str(
                        cand.get("synergy_explanation") or ""
                    ).strip()

            # Normalize supporting_evidence to a list[str]
            ev = cand.get("supporting_evidence")
            if ev is None:
                cand["supporting_evidence"] = []
            elif not isinstance(ev, list):
                cand["supporting_evidence"] = [str(ev)]
                notes.append("supporting_evidence must be a list (normalized)")
            else:
                cand["supporting_evidence"] = [str(x) for x in ev if x is not None]

            return {"candidate": cand, "fatal_errors": fatal_errors, "notes": notes}

        primary_strategy = "tool_call" if tool_spec is not None else ("response_format" if response_format is not None else "text")

        # ===== P0: Two-stage formulation generation =====
        #
        # Stage 1 (draft): keep high-quality research reasoning (often reasoning_effort=xhigh).
        # Stage 2 (structured): disable reasoning (reasoning_effort=none) to get reliable
        # tool calls / json_schema outputs, and validate locally.
        draft_output = ""
        try:
            # Important: The prompt above includes JSON instructions for backward compatibility.
            # We override them here to get a compact, transcribable draft.
            if expected_num_components == 2:
                draft_prompt = (
                    f"{prompt}\n\n"
                    "## Stage 1 (Research Draft)\n"
                    "Propose ONE best candidate formulation.\n"
                    "IMPORTANT:\n"
                    "- Do NOT output JSON.\n"
                    "- Keep it compact, but research-grade.\n"
                    "- Include exactly the fields listed below.\n\n"
                    "Required fields:\n"
                    "- HBD: component name\n"
                    "- HBA: component name\n"
                    "- molar_ratio: use ':' separators and match HBD:HBA order\n"
                    "- reasoning: 3-6 sentences\n"
                    "- supporting_evidence: 3-8 one-line bullets\n\n"
                    "Output format (STRICT):\n"
                    "CANDIDATE:\n"
                    "HBD: ...\n"
                    "HBA: ...\n"
                    "molar_ratio: ...\n"
                    "reasoning: ...\n"
                    "supporting_evidence:\n"
                    "- ...\n"
                    "- ...\n"
                )
            else:
                draft_prompt = (
                    f"{prompt}\n\n"
                    "## Stage 1 (Research Draft)\n"
                    "Propose ONE best candidate formulation.\n"
                    "IMPORTANT:\n"
                    "- Do NOT output JSON.\n"
                    "- Keep it compact, but research-grade.\n"
                    "- Include exactly the fields listed below.\n\n"
                    "Required fields:\n"
                    f"- components: exactly {expected_num_components} items; each item has name / role / function\n"
                    "- molar_ratio: use ':' separators and match the component order\n"
                    "- reasoning: 4-8 sentences\n"
                    "- supporting_evidence: 3-8 one-line bullets\n"
                    "- synergy_explanation: 3-6 sentences\n\n"
                    "Output format (STRICT):\n"
                    "CANDIDATE:\n"
                    "components:\n"
                    "  1) name=... | role=... | function=...\n"
                    "  2) ...\n"
                    "molar_ratio: ...\n"
                    "reasoning: ...\n"
                    "synergy_explanation: ...\n"
                    "supporting_evidence:\n"
                    "- ...\n"
                    "- ...\n"
                )

            draft_output = self.llm_client(
                draft_prompt,
                max_tokens=int(self.config.get("agent", {}).get("draft_max_tokens", 1400)),
            )
            draft_output = draft_output or ""
            diagnostics["draft_used"] = bool(draft_output.strip())
            diagnostics["draft_output"] = draft_output
        except Exception as e:
            logger.warning(
                "[Formulation] Draft stage failed; proceeding with one-shot structured output. Error: %s",
                str(e),
            )
            diagnostics["draft_used"] = False
            diagnostics["draft_output"] = ""
            draft_output = ""

        # Stage 2: strict structured output (transcription)
        # Use no-reasoning + temperature=0 for maximum schema reliability.
        structured_reasoning_effort = str(
            self.config.get("agent", {}).get("structured_reasoning_effort", "none")
        )
        structured_temperature = float(
            self.config.get("agent", {}).get("structured_temperature", 0.0)
        )
        structured_max_tokens = int(
            self.config.get("agent", {}).get("structured_max_tokens", 1800)
        )

        source_text = draft_output.strip() if draft_output.strip() else prompt
        if expected_num_components == 2:
            transcribe_prompt = (
                "You are a strict JSON transcriber.\n"
                "Convert the following formulation draft into a JSON object that EXACTLY matches the required schema.\n"
                "Rules:\n"
                "- You MUST output formulation.HBD (string), formulation.HBA (string), formulation.molar_ratio (string).\n"
                "- molar_ratio MUST have exactly 1 ':' separator (HBD:HBA).\n"
                "- reasoning MUST be a non-empty string.\n"
                "- supporting_evidence MUST be a JSON array of strings.\n"
                "- Do NOT introduce components not present in the draft.\n"
                "- Output ONLY the final structured object; no extra text.\n\n"
                "Draft:\n"
                f"{source_text}\n"
            )
        else:
            transcribe_prompt = (
                "You are a strict JSON transcriber.\n"
                "Convert the following formulation draft into a JSON object that EXACTLY matches the required schema.\n"
                "Rules:\n"
                f"- You MUST output exactly {expected_num_components} components.\n"
                "- components MUST be a JSON array.\n"
                "- Each component MUST have non-empty string fields: name, role, function.\n"
                f"- molar_ratio MUST have {expected_num_components - 1} ':' separators.\n"
                "- synergy_explanation MUST be a non-empty string.\n"
                "- supporting_evidence MUST be a JSON array of strings.\n"
                "- Do NOT invent new components not present in the draft.\n"
                "- If a required field is missing for an existing component, fill it in conservatively.\n"
                "- Output ONLY the final structured object; no extra text.\n\n"
                "Draft:\n"
                f"{source_text}\n"
            )

        # Attempt 1 (structured transcribe)
        attempt1 = _attempt(
            transcribe_prompt,
            strategy=primary_strategy,
            reasoning_effort=structured_reasoning_effort,
            temperature=structured_temperature,
            max_tokens=structured_max_tokens,
        )
        cand = attempt1["candidate"]
        fatal_errors = attempt1.get("fatal_errors") or []
        notes = attempt1.get("notes") or []

        # Auto-repair once if parse/validation failed.
        if cand is None or fatal_errors:
            # If tool calling did not produce any structured output (e.g. tools unsupported
            # by SDK/proxy), fall back to response_format (if available) using the full prompt.
            if primary_strategy == "tool_call" and not diagnostics.get("used_tool_call"):
                secondary = "response_format" if response_format is not None else "text"
                attempt2 = _attempt(
                    transcribe_prompt,
                    strategy=secondary,
                    reasoning_effort=structured_reasoning_effort,
                    temperature=structured_temperature,
                    max_tokens=structured_max_tokens,
                )
            else:
                diagnostics["repair_used"] = True
                repair_prompt = self._build_formulation_repair_prompt(
                    task=task,
                    expected_num_components=expected_num_components,
                    previous_output=(diagnostics["raw_outputs"][-1] if diagnostics["raw_outputs"] else ""),
                    errors=(fatal_errors or ["unparseable JSON output"]),
                )
                attempt2 = _attempt(
                    repair_prompt,
                    strategy=primary_strategy,
                    reasoning_effort=structured_reasoning_effort,
                    temperature=structured_temperature,
                    max_tokens=structured_max_tokens,
                )
            cand2 = attempt2["candidate"]
            fatal_errors2 = attempt2.get("fatal_errors") or []
            notes2 = attempt2.get("notes") or []

            # Keep the repaired candidate even if still invalid (for debugging),
            # but record validation status accurately.
            if cand2 is not None:
                cand = cand2
            fatal_errors = fatal_errors2
            notes = notes2

        diagnostics["parse_ok"] = cand is not None
        diagnostics["validation_ok"] = bool(cand is not None and not fatal_errors)
        diagnostics["validation_errors"] = fatal_errors
        diagnostics["notes"] = notes

        if cand is None:
            # Total failure: no JSON to persist.
            return {
                "formulation": {},
                "reasoning": "Formulation generation failed: no JSON output could be parsed.",
                "confidence": 0.0,
                "supporting_evidence": [],
                "_diagnostics": diagnostics,
            }

        # Attach diagnostics (kept out of the prompt; safe for persistence).
        cand["_diagnostics"] = diagnostics
        return cand

    def _build_formulation_parameters_schema(
        self, expected_num_components: int
    ) -> Optional[Dict[str, Any]]:
        """Build a JSON Schema for the formulation output object."""
        if expected_num_components < 2:
            return None

        if expected_num_components == 2:
            return {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "formulation": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "HBD": {"type": "string"},
                            "HBA": {"type": "string"},
                            "molar_ratio": {"type": "string"},
                        },
                        "required": ["HBD", "HBA", "molar_ratio"],
                    },
                    "reasoning": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "supporting_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["formulation", "reasoning", "confidence", "supporting_evidence"],
            }

        component_item = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "role": {"type": "string"},
                "function": {"type": "string"},
            },
            "required": ["name", "role", "function"],
        }

        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "formulation": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "components": {
                            "type": "array",
                            "items": component_item,
                            "minItems": expected_num_components,
                            "maxItems": expected_num_components,
                        },
                        "molar_ratio": {"type": "string"},
                        "num_components": {"type": "integer"},
                    },
                    "required": ["components", "molar_ratio", "num_components"],
                },
                "reasoning": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "supporting_evidence": {"type": "array", "items": {"type": "string"}},
                "synergy_explanation": {"type": "string"},
            },
            "required": [
                "formulation",
                "reasoning",
                "confidence",
                "supporting_evidence",
                "synergy_explanation",
            ],
        }

    def _build_formulation_tool_spec(
        self, expected_num_components: int
    ) -> Optional[Tuple[List[Dict[str, Any]], Any, str]]:
        """
        Build OpenAI Chat Completions tool spec for strict function calling outputs.

        Returns:
            (tools, tool_choice, function_name) or None if unsupported.
        """
        schema = self._build_formulation_parameters_schema(expected_num_components)
        if schema is None:
            return None

        fn_name = "emit_des_formulation_v1"
        tools = [
            {
                "type": "function",
                "function": {
                    "name": fn_name,
                    "description": (
                        "Emit a DES formulation object that strictly matches the required JSON schema."
                    ),
                    "strict": True,
                    "parameters": schema,
                },
            }
        ]

        tool_choice = {"type": "function", "function": {"name": fn_name}}
        return tools, tool_choice, fn_name

    def _build_formulation_response_format(self, expected_num_components: int) -> Optional[Dict[str, Any]]:
        """
        Build OpenAI Chat Completions `response_format` for structured outputs.

        Notes:
        - We keep the schema intentionally simple (supported subset) so it works
          across OpenAI-compatible deployments.
        - Even with strict schema, we still validate locally before persisting.
        """
        schema = self._build_formulation_parameters_schema(expected_num_components)
        if schema is None:
            return None

        return {
            "type": "json_schema",
            "json_schema": {
                "name": "des_formulation_v1",
                "strict": True,
                "schema": schema,
            },
        }

    def _build_formulation_repair_prompt(
        self,
        *,
        task: Dict,
        expected_num_components: int,
        previous_output: str,
        errors: List[str],
    ) -> str:
        """
        Repair prompt that converts a non-JSON/invalid output into valid JSON.

        We intentionally keep this repair prompt small: it should reformat the
        model's *previous* answer instead of re-querying all tool context.
        """
        if expected_num_components == 2:
            schema_hint = (
                "Return JSON with keys: formulation(HBD,HBA,molar_ratio), reasoning, confidence, supporting_evidence.\n"
                "Template:\n"
                '{"formulation":{"HBD":"...","HBA":"...","molar_ratio":"..."},"reasoning":"...","confidence":0.0,"supporting_evidence":["..."]}\n'
            )
        else:
            ratio_example = ":".join(["1"] * expected_num_components)
            component_lines: List[str] = []
            for i in range(1, expected_num_components + 1):
                comma = "," if i < expected_num_components else ""
                component_lines.append(
                    f'      {{"name": "Component {i}", "role": "HBD/HBA/neutral", "function": "..."}}{comma}'
                )
            components_block = "\n".join(component_lines)
            schema_hint = (
                f"- Must be exactly {expected_num_components} components.\n"
                f"- molar_ratio must have {expected_num_components - 1} ':' separators.\n"
                "Return JSON with keys: formulation(components,molar_ratio,num_components), reasoning, confidence, supporting_evidence, synergy_explanation.\n"
                "Template:\n"
                "{\n"
                '  "formulation": {\n'
                '    "components": [\n'
                f"{components_block}\n"
                "    ],\n"
                f'    "molar_ratio": "{ratio_example}",\n'
                f'    "num_components": {expected_num_components}\n'
                "  },\n"
                '  "reasoning": "...",\n'
                '  "confidence": 0.0,\n'
                '  "supporting_evidence": ["..."],\n'
                '  "synergy_explanation": "..." \n'
                "}\n"
            )

        err_text = "\n".join(f"- {e}" for e in (errors or [])[:12])
        return f"""You previously generated an answer for a DES formulation task, but it did not meet the required JSON schema.

Task:
- description: {task.get('description')}
- target_material: {task.get('target_material')}
- target_temperature_C: {task.get('target_temperature', 25)}
- num_components: {expected_num_components}

Errors detected:
{err_text}

Requirements:
{schema_hint}Return ONLY a JSON object that matches the schema from the instructions in the previous prompt.
Do NOT include markdown fences. Do NOT include any extra text outside the JSON.

Your previous output:
{previous_output}
"""

    def _build_formulation_prompt(
        self,
        task: Dict,
        memories: List[MemoryItem],
        theory_list: List[Dict],  # Changed
        literature_list: List[Dict]  # Changed
    ) -> str:
        """
        Build comprehensive prompt for formulation generation.

        Args:
            task: Task dictionary
            memories: Retrieved memories
            theory_list: List of theory knowledge
            literature_list: List of literature knowledge

        Returns:
            Formatted prompt string
        """
        prompt = "# DES Formulation Design Task\n\n"

        # Task description
        prompt += f"## Task\n{task['description']}\n\n"
        prompt += f"**Target Material:** {task['target_material']}\n"
        prompt += f"**Target Temperature:** {task.get('target_temperature', 25)}°C\n"

        constraints = task.get("constraints", {})
        if constraints:
            prompt += f"**Constraints:** {constraints}\n"

        prompt += "\n"

        # Inject memories
        if memories:
            prompt += format_memories_for_prompt(memories)
            prompt += "\n"

        # Add all accumulated theory knowledge
        if theory_list:
            prompt += f"## Theoretical Knowledge (from CoreRAG - {len(theory_list)} queries)\n\n"
            for i, theory in enumerate(theory_list, 1):
                prompt += f"### Theory Query {i}\n{self._format_corerag_for_prompt(theory)}\n\n"

        # Add all accumulated literature knowledge
        if literature_list:
            prompt += f"## Literature Precedents (from LargeRAG - {len(literature_list)} queries)\n\n"
            for i, literature in enumerate(literature_list, 1):
                prompt += f"### Literature Query {i}\n{self._format_largerag_for_prompt(literature)}\n\n"

        # Instructions - Support both binary and multi-component DES
        num_components = task.get("num_components", 2)  # Default to binary (2-component) DES

        if num_components == 2:
            # Binary DES (traditional format)
            prompt += """## Instructions

Based on the above information, design a **binary DES formulation** (2 components). Your output must include:

1. **HBD (Hydrogen Bond Donor)**: Component name
2. **HBA (Hydrogen Bond Acceptor)**: Component name
3. **Molar Ratio**: e.g., "1:2" (HBD:HBA)
4. **Reasoning**: Explain your design choices (2-3 sentences)
5. **Confidence**: 0.0 to 1.0
6. **Supporting Evidence**: List key facts from memory/theory/literature

Format your response as JSON:
```json
{
    "formulation": {
        "HBD": "...",
        "HBA": "...",
        "molar_ratio": "..."
    },
    "reasoning": "...",
    "confidence": 0.0,
    "supporting_evidence": ["...", "..."]
}
```
"""
        else:
            # Multi-component DES (ternary, quaternary, etc.)
            prompt += f"""## Instructions

Based on the above information, design a **{num_components}-component DES formulation** (multi-component eutectic system). Your output must include:

1. **Components**: List of {num_components} components with their roles (HBD/HBA/neutral)
2. **Molar Ratio**: Ratio between all components (e.g., "1:2:1" for ternary)
3. **Reasoning**: Explain your design choices, especially why multiple components are beneficial (3-4 sentences)
4. **Confidence**: 0.0 to 1.0
5. **Supporting Evidence**: List key facts from memory/theory/literature. Left empty if none.
6. **Synergy Explanation**: How do the multiple components work together?

Format your response as JSON:
```json
{{
    "formulation": {{
        "components": [
            {{"name": "Component 1", "role": "HBD", "function": "Primary hydrogen bond donor"}},
            {{"name": "Component 2", "role": "HBA", "function": "Hydrogen bond acceptor"}},
            {{"name": "Component 3", "role": "HBD/modifier", "function": "Secondary donor to tune properties"}}
        ],
        "molar_ratio": "1:2:0.5",
        "num_components": {num_components}
    }},
    "reasoning": "...",
    "confidence": 0.0,
    "supporting_evidence": ["...", "..."],
    "synergy_explanation": "Component X and Y synergistically..."
}}
```

**Key Design Considerations for Multi-Component DES**:
- **Ternary DES (3 components)**: Often used to tune viscosity, melting point, or solubility
- **Quaternary DES (4+ components)**: Can provide fine-tuned properties but increase complexity
- **Synergy**: Multiple HBDs or HBAs can create cooperative effects
- **Literature precedent**: Check if similar multi-component systems have been reported
"""


        return prompt

    def _format_corerag_for_prompt(self, theory: Any) -> str:
        """
        Convert CoreRAG output into a prompt-friendly, information-dense text block.

        Rationale:
        - CoreRAG may return large nested dicts. Dumping raw dicts bloats context and
          hurts LLM performance.
        - CoreRAG already produces a high-quality formatted report schema
          (summary/key_points/background_information/relationships). Prefer that.
        """
        if not theory:
            return "(No CoreRAG result)"

        if not isinstance(theory, dict):
            # Defensive fallback (keep information; never crash prompt build)
            return str(theory)

        query_text = (theory.get("_query_text") or theory.get("query") or "").strip()
        summary = (theory.get("summary") or "").strip()
        key_points = theory.get("key_points") or []
        background = theory.get("background_information") or []
        relationships = theory.get("relationships") or []

        parts: List[str] = []
        if query_text:
            parts.append(f"Query: {query_text}")
        if summary:
            parts.append(f"Summary: {summary}")

        if key_points:
            parts.append("Key Points:")
            for p in key_points:
                if p:
                    parts.append(f"- {p}")

        if background:
            parts.append("Background Information:")
            for b in background:
                if b:
                    parts.append(f"- {b}")

        if relationships:
            parts.append("Relationships:")
            for r in relationships:
                if r:
                    parts.append(f"- {r}")

        if parts:
            return "\n".join(parts).strip()

        # Last resort: stable JSON pretty-print with string fallback for unknown objects.
        try:
            return json.dumps(to_jsonable(theory), ensure_ascii=False, indent=2)
        except Exception:
            return str(theory)

    def _format_largerag_for_prompt(self, literature: Any) -> str:
        """
        Convert LargeRAG output into a prompt-friendly text block.

        Rationale:
        - LargeRAG returns `documents` which may contain long page texts.
          Dumping the raw dict often exceeds LLM context limits.
        - Prefer `formatted_text` which is already truncated and readable.
        """
        if not literature:
            return "(No LargeRAG result)"

        if not isinstance(literature, dict):
            return str(literature)

        query_text = (literature.get("_query_text") or literature.get("query") or "").strip()
        formatted_text = literature.get("formatted_text")
        if isinstance(formatted_text, str) and formatted_text.strip():
            if query_text:
                return f"Query: {query_text}\n\n{formatted_text.strip()}"
            return formatted_text.strip()

        # Fallback: format documents but NEVER include full unbounded text.
        documents = literature.get("documents") or []
        if isinstance(documents, list) and documents:
            parts: List[str] = []
            if query_text:
                parts.append(f"Query: {query_text}")
                parts.append("")

            for i, doc in enumerate(documents, 1):
                if not isinstance(doc, dict):
                    parts.append(f"Document {i}: {str(doc)}")
                    continue

                meta = doc.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}

                doc_hash = str(meta.get("doc_hash", "unknown"))[:8]
                page = meta.get("page_idx", "N/A")
                score = doc.get("score", 0.0)

                text = doc.get("text", "")
                if not isinstance(text, str):
                    text = str(text)
                if len(text) > 600:
                    text = text[:600] + "..."

                parts.append(
                    f"Document {i} (Score: {float(score):.3f}, Source: {doc_hash}..., Page: {page}):\n{text}"
                )

            return "\n\n---\n\n".join(parts).strip()

        # Last resort: stable JSON pretty-print
        try:
            return json.dumps(to_jsonable(literature), ensure_ascii=False, indent=2)
        except Exception:
            return str(literature)

    def _parse_formulation_output(self, llm_output: str) -> Dict:
        """
        Parse LLM output to extract formulation.

        Args:
            llm_output: Raw LLM output

        Returns:
            Structured formulation dict
        """
        import json
        import re

        # Try to extract JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', llm_output, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                return result
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON from LLM output")

        # Fallback: return minimal structure
        return {
            "formulation": {},
            "reasoning": llm_output[:500],
            "confidence": 0.5,
            "supporting_evidence": []
        }

    # ===== NEW: Asynchronous Experimental Feedback Methods =====

    def submit_experiment_feedback(
        self,
        recommendation_id: str,
        experiment_result: ExperimentResult
    ) -> Dict:
        """
        Submit experimental feedback for a recommendation (NEW: Async feedback loop).

        This method completes the async feedback loop:
        1. User performs experiment based on recommendation
        2. User submits ExperimentResult with lab measurements
        3. System extracts data-driven memories
        4. System consolidates new memories into ReasoningBank

        Args:
            recommendation_id: ID of the recommendation to update
            experiment_result: ExperimentResult object with lab measurements

        Returns:
            Dict with processing results:
                - status: "success" or "error"
                - recommendation_id: The updated recommendation ID
                - measurement_count: Number of measurement rows processed
                - is_liquid_formed: Whether DES liquid formed
                - memories_extracted: List of extracted memory titles
                - message: Human-readable status message

        Example:
            Typical usage:
                - Prepare ExperimentResult with measurements list
                - Call submit_experiment_feedback(rec_id, experiment_result)
                - Inspect returned measurement_count / memories_extracted
        """
        logger.info(f"Processing experimental feedback for recommendation {recommendation_id}")

        try:
            # Check if this is an update (recommendation already has feedback)
            existing_rec = self.rec_manager.get_recommendation(recommendation_id)
            is_update = (
                existing_rec is not None and
                existing_rec.experiment_result is not None and
                existing_rec.trajectory.metadata.get("feedback_processed_at") is not None
            )

            if is_update:
                logger.info(f"Detected feedback update for {recommendation_id}")

            # Step 1: Submit experimental feedback to recommendation manager
            self.rec_manager.submit_feedback(recommendation_id, experiment_result)

            # Step 2: Use FeedbackProcessor to extract memories and update ReasoningBank
            process_result = self.feedback_processor.process_feedback(
                recommendation_id,
                is_update=is_update
            )

            # Log using measurement count
            solubility_str = f"measurements={process_result.get('measurement_count', 0)}"
            logger.info(
                f"Feedback processing completed: {process_result['num_memories']} "
                f"memories extracted ({solubility_str})"
            )

            # Auto-save if configured
            if self.config.get("memory", {}).get("auto_save", False):
                save_path = self.config["memory"]["persist_path"]
                self.memory.save(save_path)
                logger.info(f"Auto-saved memory bank to {save_path}")

            message = (
                f"Experimental feedback processed successfully. "
                f"Measurements: {process_result.get('measurement_count', 0)}. "
            )
            if is_update:
                deleted = process_result.get("deleted_memories", 0)
                message += f"Updated feedback (deleted {deleted} old memories). "
            message += f"Extracted {process_result['num_memories']} new memories."

            return {
                "status": "success",
                "recommendation_id": recommendation_id,
                "is_liquid_formed": process_result.get("is_liquid_formed"),
                "memories_extracted": process_result["memories_extracted"],
                "measurement_count": process_result.get("measurement_count", 0),
                "is_update": is_update,
                "deleted_memories": process_result.get("deleted_memories", 0) if is_update else None,
                "message": message
            }

        except Exception as e:
            logger.error(f"Failed to process experimental feedback: {e}")
            return {
                "status": "error",
                "recommendation_id": recommendation_id,
                "message": f"Error processing feedback: {str(e)}"
            }

    def load_historical_recommendations(
        self,
        data_path: str,
        reprocess: bool = True
    ) -> Dict:
        """
        Load historical recommendations from another system instance (NEW: Cross-instance reuse).

        This enables transferring experimental knowledge between different system instances:
        - System A generates recommendations + collects experiments
        - System B loads System A's data and learns from it
        - Version-aware data format ensures backward compatibility

        Args:
            data_path: Path to directory containing recommendations.json or individual REC_*.json files
            reprocess: If True, re-extract memories with current extraction logic (default: True)
                      If False, only load existing memories without reprocessing

        Returns:
            Dict with loading results:
                - status: "success" or "error"
                - num_loaded: Number of recommendations loaded
                - num_reprocessed: Number re-processed with current logic
                - memories_added: Total memories added to ReasoningBank
                - message: Human-readable status

        Example:
            >>> # Load data from System A into System B
            >>> result = agent_B.load_historical_recommendations(
            ...     data_path="/path/to/system_A/recommendations/",
            ...     reprocess=True  # Re-extract with System B's logic
            ... )
            >>> print(f"Loaded {result['num_loaded']} recommendations")
            >>> print(f"Added {result['memories_added']} memories to System B")
        """
        logger.info(f"Loading historical recommendations from {data_path}")

        try:
            import os
            import json
            from pathlib import Path

            data_dir = Path(data_path)
            if not data_dir.exists():
                raise FileNotFoundError(f"Data path not found: {data_path}")

            num_loaded = 0
            num_reprocessed = 0
            total_memories = 0

            # Load all recommendation JSON files
            rec_files = list(data_dir.glob("REC_*.json"))

            for rec_file in rec_files:
                try:
                    with open(rec_file, "r", encoding="utf-8") as f:
                        rec_data = json.load(f)

                    # Convert to Recommendation object (version-aware deserialization)
                    rec = Recommendation.from_dict(rec_data)

                    # Only process COMPLETED recommendations with experimental feedback
                    if rec.status == "COMPLETED" and rec.experiment_result is not None:
                        num_loaded += 1

                        if reprocess:
                            # Re-extract memories with current extraction logic
                            logger.info(f"Reprocessing {rec.recommendation_id} with current logic")

                            new_memories = self.extractor.extract_from_experiment(
                                rec.trajectory,
                                rec.experiment_result
                            )

                            if new_memories:
                                self.memory.consolidate(new_memories)
                                total_memories += len(new_memories)
                                num_reprocessed += 1
                                logger.info(
                                    f"Extracted {len(new_memories)} memories from {rec.recommendation_id}"
                                )
                        else:
                            # Just load existing memories (if stored in trajectory metadata)
                            existing_memories = rec.trajectory.metadata.get("extracted_memories", [])
                            total_memories += len(existing_memories)
                            logger.info(
                                f"Loaded {len(existing_memories)} existing memories from {rec.recommendation_id}"
                            )

                    else:
                        logger.debug(
                            f"Skipping {rec.recommendation_id} (status={rec.status}, "
                            f"has_feedback={rec.experiment_result is not None})"
                        )

                except Exception as e:
                    logger.warning(f"Failed to load {rec_file}: {e}")
                    continue

            # Auto-save if configured
            if self.config.get("memory", {}).get("auto_save", False):
                save_path = self.config["memory"]["persist_path"]
                self.memory.save(save_path)
                logger.info(f"Auto-saved memory bank to {save_path}")

            logger.info(
                f"Historical data loading complete: {num_loaded} recommendations loaded, "
                f"{num_reprocessed} reprocessed, {total_memories} memories added"
            )

            return {
                "status": "success",
                "num_loaded": num_loaded,
                "num_reprocessed": num_reprocessed,
                "memories_added": total_memories,
                "message": (
                    f"Successfully loaded {num_loaded} recommendations. "
                    f"Reprocessed {num_reprocessed} with current logic. "
                    f"Added {total_memories} memories to ReasoningBank."
                )
            }

        except Exception as e:
            logger.error(f"Failed to load historical recommendations: {e}")
            return {
                "status": "error",
                "num_loaded": 0,
                "num_reprocessed": 0,
                "memories_added": 0,
                "message": f"Error loading historical data: {str(e)}"
            }


# Example usage and testing
if __name__ == "__main__":
    # This will be implemented in examples/example_des_task.py
    pass
