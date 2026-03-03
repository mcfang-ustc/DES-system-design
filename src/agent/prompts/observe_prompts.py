"""
Prompts for LLM-based OBSERVE phase in ReAct loop

These prompts guide the LLM to analyze action results and generate
structured observations with insights, gaps, and recommendations.
"""

OBSERVE_PROMPT = """You are analyzing the result of a research action in DES (Deep Eutectic Solvent) formulation design.

**Task Context**:
- **Task**: {task_description}
- **Target Material**: {target_material}
- **Target Temperature**: {target_temperature}°C
- **Current Iteration**: {iteration}/{max_iterations} ({progress_pct}% complete, {stage} stage)

**Action Executed**: {action}
**Action Success**: {success}

**Action Result Details**:
{action_result_summary}

**Current Knowledge State**:
- Memories retrieved: {has_memories} ({num_memories} items)
- Theoretical knowledge (CoreRAG): {num_theory} queries completed (failed: {failed_theory})
- Literature knowledge (LargeRAG): {num_literature} queries completed (failed: {failed_literature})
- Formulation candidates: {num_formulations} generated
- Previous observations: {num_observations} recorded

**Recent Observations** (last 2 iterations):
{recent_observations}

---

## Your Task

Analyze the action result and provide structured insights to guide the agent's next steps.

**Analysis Guidelines**:

1. **Summary**: Concisely describe what was gained or lost (1-2 sentences)
   - Focus on actionable information, not just status
   - Highlight quantitative data when available

2. **Knowledge Updated**: Which knowledge domains were updated?
   - Options: "memories", "theory", "literature", "formulation"
   - Only include domains with NEW information

3. **Key Insights**: Extract 1-3 valuable insights from this action
   - Identify patterns, contradictions, or unexpected findings
   - Include quantitative details (e.g., "6 papers recommend 1:2 ratio")
   - Note connections between different knowledge sources

4. **Information Gaps**: Identify 1-3 critical missing pieces
   - What information is still needed to generate a confident formulation?
   - Be specific (e.g., "No viscosity data at 25°C" vs "Need more data")
   - Prioritize gaps that directly impact formulation design

5. **Information Sufficient**: Can we generate a formulation now?
   - Consider: Do we have HBD/HBA candidates + molar ratio guidance + supporting evidence?
   - Standards: Early stage (40%+ progress) → need memories OR theory+literature
   - Mid stage (40-75%) → need at least 2 knowledge sources
   - Late stage (75%+) → generate even with limited knowledge

6. **Recommended Next Action**: Suggest the most valuable next action
   - Options: retrieve_memories, query_theory, query_literature, query_parallel, generate_formulation, refine_formulation, finish
   - Base recommendation on: information gaps + progress stage + tool availability
   - **CRITICAL**: If a tool has failed 2+ times, DO NOT recommend it again
   - **Parallel policy**: Do NOT recommend query_parallel as a default. Recommend it only when you explicitly need BOTH theory + literature AND at least one is still missing. If both are already available, prefer generate_formulation/refine_formulation.

**Special Considerations**:

- **Empty results are acceptable**: If retrieve_memories returns 0, this just means no historical data exists (not a failure)
- **Tool failure tracking**: CoreRAG failed {failed_theory} times, LargeRAG failed {failed_literature} times
  - If failures >= 2, recommend alternative actions
- **Progress awareness**: At {progress_pct}% complete, balance thoroughness vs. efficiency
  - Early stage: Focus on knowledge gathering
  - Late stage: Prioritize formulation generation

---

## Output Format

Respond with ONLY a valid JSON object (no markdown, no explanation):

{{
    "summary": "<1-2 sentence summary of what was gained/lost>",
    "knowledge_updated": ["domain1", "domain2"],
    "key_insights": [
        "<insight 1 with specific details>",
        "<insight 2 with specific details>"
    ],
    "information_gaps": [
        "<specific gap 1>",
        "<specific gap 2>"
    ],
    "information_sufficient": true/false,
    "recommended_next_action": "<action_name>",
    "recommendation_reasoning": "<1 sentence explaining why this action is recommended>"
}}

**Example Output**:
{{
    "summary": "Retrieved 10 literature papers on cellulose-DES systems. All papers recommend ChCl as HBD, with glycerol (6/10) and urea (4/10) as top HBAs.",
    "knowledge_updated": ["literature"],
    "key_insights": [
        "Glycerol-based DES dominate recent publications (60%), suggesting higher performance than urea",
        "Optimal molar ratios consistently reported as 1:2 to 1:3 (HBD:HBA)",
        "Most studies focus on 40-80°C range; only 2/10 papers report 25°C data"
    ],
    "information_gaps": [
        "Lack low-temperature (25°C) leaching-efficiency data for most formulations",
        "No viscosity measurements found in retrieved literature",
        "Missing comparative performance data between glycerol and urea at target temperature"
    ],
    "information_sufficient": false,
    "recommended_next_action": "query_theory",
    "recommendation_reasoning": "Have literature precedents but need theoretical understanding of why glycerol outperforms urea at low temperature to make informed selection"
}}

Now analyze the action result:"""


def format_action_result_for_observe(action: str, action_result: dict, knowledge_state: dict) -> str:
    """
    Format action result details for OBSERVE prompt.

    Args:
        action: Action that was executed
        action_result: Result dict from _act method
        knowledge_state: Current knowledge state

    Returns:
        Formatted string describing the result
    """
    result_text = f"**Action**: {action}\n"
    result_text += f"**Success**: {action_result.get('success', False)}\n"
    result_text += f"**Summary**: {action_result.get('summary', 'No summary available')}\n\n"

    # Add action-specific details
    if action == "retrieve_memories":
        memories = action_result.get("data", [])
        result_text += f"**Memories Retrieved**: {len(memories)}\n"
        if len(memories) > 0:
            result_text += "**Sample Memories (experiment summaries if available)**:\n"
            for i, mem in enumerate(memories[:2], 1):
                title = mem.title[:80] if hasattr(mem, 'title') else "Unknown"
                metadata = mem.metadata if hasattr(mem, 'metadata') else {}

                summary = metadata.get("experiment_summary_text")
                if not summary:
                    # Fallback: derive a short leaching efficiency snippet
                    measurements = metadata.get("measurements", []) or []
                    max_eff = None
                    unit = "%"
                    for m in measurements:
                        if m.get("leaching_efficiency") is not None:
                            val = m.get("leaching_efficiency")
                            max_eff = val if max_eff is None else max(max_eff, val)
                            unit = m.get("unit", unit)
                    if max_eff is not None:
                        summary = f"Max leaching efficiency ≈ {max_eff} {unit}"
                    else:
                        summary = "No leaching data provided"

                summary_short = summary if len(summary) <= 240 else summary[:240] + "..."
                result_text += f"  {i}. {title} — {summary_short}\n"

    elif action == "query_theory":
        theory = action_result.get("data")
        if theory:
            theory_preview = str(theory)[:300] if theory else "No data"
            result_text += f"**Theory Knowledge Preview**: {theory_preview}...\n"
        else:
            result_text += "**Theory Knowledge**: Query failed or returned no results\n"

    elif action == "query_literature":
        literature = action_result.get("data")
        if literature:
            lit_preview = str(literature)[:300] if literature else "No data"
            result_text += f"**Literature Data Preview**: {lit_preview}...\n"
        else:
            result_text += "**Literature Data**: Query failed or returned no results\n"

    elif action == "query_parallel":
        theory = action_result.get("data", {}).get("theory")
        literature = action_result.get("data", {}).get("literature")
        result_text += f"**Theory Retrieved**: {'Yes' if theory else 'No'}\n"
        result_text += f"**Literature Retrieved**: {'Yes' if literature else 'No'}\n"

    elif action in ["generate_formulation", "refine_formulation"]:
        formulation = action_result.get("data", {})
        result_text += f"**Formulation**: {formulation.get('formulation', {})}\n"
        result_text += f"**Confidence**: {formulation.get('confidence', 0.0)}\n"
        result_text += f"**Reasoning**: {formulation.get('reasoning', 'N/A')[:200]}...\n"

    return result_text


def parse_observe_output(llm_output: str) -> dict:
    """
    Parse LLM output from OBSERVE phase.

    Expected format: Pure JSON object

    Args:
        llm_output: Raw output from LLM

    Returns:
        Dict with observation fields
    """
    import json
    import re

    # Try to extract JSON (handle potential markdown wrapping)
    json_match = re.search(r'```json\s*(.*?)\s*```', llm_output, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to parse as direct JSON
    try:
        return json.loads(llm_output.strip())
    except json.JSONDecodeError:
        pass

    # Fallback: return minimal structure
    return {
        "summary": "Failed to parse observation",
        "knowledge_updated": [],
        "key_insights": [],
        "information_gaps": [],
        "information_sufficient": False,
        "recommended_next_action": "generate_formulation",
        "recommendation_reasoning": "Fallback action due to parsing error"
    }
