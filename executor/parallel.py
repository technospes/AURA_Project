"""
PARALLEL EXECUTION EXTENSION
==============================
Extends ExecutionRunner to run independent steps in parallel.

Steps marked with "parallel_group": N run simultaneously.
Steps with "depends_on" run only after their dependencies finish.

Example plan with parallelism:
  Step 0: open spotify           (group=0, sequential)
  Step 1: search music [group=1] (parallel with step 2)
  Step 2: update context [grp=1] (parallel with step 1)
  Step 3: play result            (depends_on=[1], sequential)

Also adds step prioritization: HIGH priority steps run first within a group.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ParallelExecutionMixin:
    """
    Mix this into ExecutionRunner to add parallel step execution.
    
    Usage: Replace run_plan() in ExecutionRunner with this version.
    """

    async def run_plan_parallel(
        self,
        plan: List[Dict],
        intent: Dict,
        context: Dict
    ) -> List[Dict]:
        """
        Execute plan with parallelism for independent steps.
        
        Steps are grouped by "parallel_group" key.
        Within a group, all steps run simultaneously.
        Groups run in order (group 0 → group 1 → group 2...).
        Steps with depends_on override group ordering.
        """
        if not plan:
            return []

        # Check if any step uses parallel_group
        has_parallel = any(s.get("parallel_group") is not None for s in plan)
        if not has_parallel:
            # No parallelism specified — fall back to sequential
            return await self.run_plan(plan, intent, context)

        # Group steps
        groups = self._group_steps(plan)
        all_results: List[Dict] = [None] * len(plan)
        self._step_results = all_results

        for group_id in sorted(groups.keys()):
            group_steps = groups[group_id]

            # Sort by priority within group
            group_steps.sort(
                key=lambda x: x[1].get("priority", "normal") == "high",
                reverse=True
            )

            if len(group_steps) == 1:
                # Single step — execute normally
                idx, step = group_steps[0]
                if self._deps_satisfied(step, idx):
                    result = await self._execute_with_retry(step, idx, intent, context)
                else:
                    result = self._skipped_result(idx, step)
                all_results[idx] = result
                self._step_results = all_results
            else:
                # Multiple steps — execute in parallel
                logger.info(f"  ⚡ Parallel group {group_id}: {len(group_steps)} steps")
                tasks = []
                indices = []
                for idx, step in group_steps:
                    if self._deps_satisfied(step, idx):
                        tasks.append(self._execute_with_retry(step, idx, intent, context))
                        indices.append(idx)
                    else:
                        all_results[idx] = self._skipped_result(idx, step)

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for i, (idx, result) in enumerate(zip(indices, results)):
                        if isinstance(result, Exception):
                            all_results[idx] = {
                                "step": idx,
                                "action": plan[idx]["action"],
                                "success": False,
                                "error": str(result),
                                "output": None,
                                "duration_ms": 0
                            }
                        else:
                            all_results[idx] = result
                    self._step_results = all_results

        return [r for r in all_results if r is not None]

    def _group_steps(self, plan: List[Dict]) -> Dict[int, List[Tuple[int, Dict]]]:
        """Group steps by parallel_group. Ungrouped steps get their own sequential group."""
        groups: Dict[int, List] = {}
        sequential_counter = 1000  # High numbers for sequential steps

        for idx, step in enumerate(plan):
            group_id = step.get("parallel_group")
            if group_id is None:
                # Sequential step — give it a unique group
                group_id = sequential_counter
                sequential_counter += 1

            if group_id not in groups:
                groups[group_id] = []
            groups[group_id].append((idx, step))

        return groups

    def _skipped_result(self, idx: int, step: Dict) -> Dict:
        return {
            "step": idx,
            "action": step.get("action", ""),
            "success": False,
            "error": "Skipped: dependency failed",
            "output": None,
            "duration_ms": 0
        }


def add_parallel_group(step: Dict, group_id: int, priority: str = "normal") -> Dict:
    """Helper to annotate a step with a parallel group."""
    step["parallel_group"] = group_id
    step["priority"]       = priority
    return step
