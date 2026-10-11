"""Topological workflow dependency graph, node reachability, and critical path analysis.

Notes/Architectural Intent:
    Provides structural graph analysis for WorkflowDefinition instances.
    Enables ancestor/descendant traversals, blast radius failure projection,
    longest-latency critical path analysis, and decoupled rendering across
    visualization targets without modifying the underlying workflow definition.
"""

from collections import deque
from graphlib import TopologicalSorter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from hexaflow.domain.exceptions import StepNotFoundError
from hexaflow.domain.models import (
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
    evaluate_trigger_rule,
)
from hexaflow.domain.state import StepStatus


class GraphNode(BaseModel):
    """Enriched topological node in a workflow dependency graph.

    Notes/Architectural Intent:
        Represents an individual step within its stage milestone, tracking direct
        upstream parents, downstream children, trigger rule policies, side-effect
        flags, and execution constraints for graph analysis.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(description="Unique step name within the workflow.")
    stage_name: str = Field(description="Name of the enclosing stage.")
    step_def: StepDefinition = Field(description="Underlying StepDefinition.")
    parents: frozenset[str] = Field(
        default_factory=frozenset,
        description="Direct upstream prerequisite step names.",
    )
    children: frozenset[str] = Field(
        default_factory=frozenset,
        description="Direct downstream dependent step names.",
    )
    side_effects: bool = Field(
        default=False,
        description="True if step mutates external state (databases, APIs, filesystem).",
    )
    has_dry_run: bool = Field(
        default=False,
        description="True if mock fallback is provided for simulation mode.",
    )
    estimated_duration_seconds: float = Field(
        default=1.0,
        ge=0.0,
        description="Estimated execution duration in seconds for critical path analysis.",
    )

    @property
    def step_id(self) -> str:
        """Alias for name."""
        return self.name

    @property
    def step(self) -> StepDefinition:
        """Alias for step_def."""
        return self.step_def

    @property
    def trigger_rule(self) -> TriggerRule:
        """Convenience property for step_def.trigger_rule."""
        return self.step_def.trigger_rule

    @property
    def is_critical_path(self) -> bool:
        """Placeholder for node-level critical path check."""
        return False


class WorkflowGraph(BaseModel):
    """Topological representation and structural analysis engine of a workflow DAG.

    Notes/Architectural Intent:
        Constructed from a WorkflowDefinition to provide rich graph queries:
        ancestors, descendants, blast radius failure projections, critical path
        latency calculation, and stage/step partitioning.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    workflow_name: str = Field(description="Unique name of the workflow.")
    workflow_version: str = Field(description="Semantic version of the workflow definition.")
    nodes: dict[str, GraphNode] = Field(
        description="Mapping of step names to enriched GraphNode instances."
    )
    stages: dict[str, tuple[str, ...]] = Field(
        description="Ordered mapping of stage names to contained step names."
    )
    topological_order: tuple[str, ...] = Field(
        description="Total topological ordering of steps across the workflow DAG."
    )

    @property
    def workflow_id(self) -> str:
        """Alias for workflow_name to support uniform workflow identification."""
        return self.workflow_name

    @property
    def edges(self) -> list[tuple[str, str]]:
        """List of directed dependency edges (parent, child) across the graph."""
        res: list[tuple[str, str]] = []
        for name, node in self.nodes.items():
            for child in sorted(node.children):
                res.append((name, child))
        return res

    def successors(self, step_name: str) -> list[str]:
        """Return direct downstream child step names for a given step."""
        return sorted(self.get_node(step_name).children)

    def predecessors(self, step_name: str) -> list[str]:
        """Return direct upstream parent step names for a given step."""
        return sorted(self.get_node(step_name).parents)

    @classmethod
    def from_workflow(cls, workflow: WorkflowDefinition) -> Self:
        """Construct a WorkflowGraph from a validated WorkflowDefinition.

        Args:
            workflow: The WorkflowDefinition to inspect.

        Returns:
            A new WorkflowGraph populated with topological relationships.

        Notes/Architectural Intent:
            Parses stage groupings, resolves forward and reverse dependencies,
            and pre-computes topological sort order using Python graphlib.
        """
        raw_nodes: dict[str, StepDefinition] = {}
        step_to_stage: dict[str, str] = {}
        stage_steps: dict[str, list[str]] = {}

        for stage in workflow.stages:
            stage_steps[stage.name] = []
            for step in stage.steps:
                raw_nodes[step.name] = step
                step_to_stage[step.name] = stage.name
                stage_steps[stage.name].append(step.name)

        # Build adjacency maps
        parents_map: dict[str, set[str]] = {
            name: set(step.depends_on) for name, step in raw_nodes.items()
        }
        children_map: dict[str, set[str]] = {name: set() for name in raw_nodes}
        for name, parents in parents_map.items():
            for p in parents:
                children_map[p].add(name)

        # Compute topological order
        sorter = TopologicalSorter(parents_map)
        topological_order = tuple(sorter.static_order())

        # Construct GraphNode records
        nodes: dict[str, GraphNode] = {}
        for name, step in raw_nodes.items():
            has_dry = step.dry_run is not None
            nodes[name] = GraphNode(
                name=name,
                stage_name=step_to_stage[name],
                step_def=step,
                parents=frozenset(parents_map[name]),
                children=frozenset(children_map[name]),
                side_effects=step.side_effects,
                has_dry_run=has_dry,
                estimated_duration_seconds=step.estimated_duration_seconds,
            )

        stages_tuple = {st_name: tuple(st_list) for st_name, st_list in stage_steps.items()}

        return cls(
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            nodes=nodes,
            stages=stages_tuple,
            topological_order=topological_order,
        )

    def get_node(self, step_name: str) -> GraphNode:
        """Retrieve the GraphNode for a given step name.

        Args:
            step_name: The step identifier to look up.

        Returns:
            The matching GraphNode.

        Raises:
            StepNotFoundError: If step_name is not present in the graph.
        """
        if step_name not in self.nodes:
            raise StepNotFoundError(
                f"Step '{step_name}' not found in workflow graph '{self.workflow_name}'."
            )
        return self.nodes[step_name]

    def ancestors(self, step_name: str) -> set[str]:
        """Compute all transitive upstream prerequisite step names for a step.

        Args:
            step_name: Target step name.

        Returns:
            Set of all ancestor step names.

        Raises:
            StepNotFoundError: If step_name is not found.
        """
        node = self.get_node(step_name)
        visited: set[str] = set()
        queue: deque[str] = deque(node.parents)

        while queue:
            current = queue.popleft()
            if current not in visited:
                visited.add(current)
                curr_node = self.nodes.get(current)
                if curr_node:
                    for parent in curr_node.parents:
                        if parent not in visited:
                            queue.append(parent)

        return visited

    def descendants(self, step_name: str) -> set[str]:
        """Compute all transitive downstream dependent step names for a step.

        Args:
            step_name: Target step name.

        Returns:
            Set of all descendant step names.

        Raises:
            StepNotFoundError: If step_name is not found.
        """
        node = self.get_node(step_name)
        visited: set[str] = set()
        queue: deque[str] = deque(node.children)

        while queue:
            current = queue.popleft()
            if current not in visited:
                visited.add(current)
                curr_node = self.nodes.get(current)
                if curr_node:
                    for child in curr_node.children:
                        if child not in visited:
                            queue.append(child)

        return visited

    def blast_radius(
        self,
        failed_step_name: str,
        failed_status: StepStatus = StepStatus.FAILED,
    ) -> set[str]:
        """Compute the impacted downstream blast radius if a step completes with failure.

        Args:
            failed_step_name: Step name that failed or was cancelled.
            failed_status: Terminal status assigned to the failed step.

        Returns:
            Set of downstream step names that would be blocked or skipped by trigger rule violations.

        Raises:
            StepNotFoundError: If failed_step_name is not found.

        Notes/Architectural Intent:
            Simulates cascading trigger rule evaluations through the downstream
            subgraph. If a step's trigger rule fails given its upstream statuses,
            it cascades to SKIPPED and impacts its own children.
        """
        self.get_node(failed_step_name)
        simulated_statuses: dict[str, StepStatus] = {failed_step_name: failed_status}
        impacted: set[str] = set()

        for step_name in self.topological_order:
            if step_name == failed_step_name:
                continue

            node = self.nodes[step_name]
            # Check if any parent of this node has a simulated failure/skip
            if not any(p in simulated_statuses for p in node.parents):
                continue

            # Evaluate trigger rule using simulated upstream states
            parent_statuses: list[StepStatus] = []
            for p in node.parents:
                parent_statuses.append(simulated_statuses.get(p, StepStatus.COMPLETED))

            satisfied = evaluate_trigger_rule(node.step_def.trigger_rule, parent_statuses)
            if not satisfied:
                impacted.add(step_name)
                simulated_statuses[step_name] = StepStatus.SKIPPED

        return impacted

    def critical_path(self) -> list[str]:
        """Compute the longest-latency path through the workflow DAG.

        Returns:
            List of step names forming the critical path from an initial root to a leaf.

        Notes/Architectural Intent:
            Evaluates dynamic programming distances over topological order using
            each node's estimated_duration_seconds. Identifies the bounding latency bottleneck.
        """
        if not self.nodes:
            return []

        dist: dict[str, float] = {}
        pred: dict[str, str | None] = {}

        for step_name in self.topological_order:
            node = self.nodes[step_name]
            duration = node.estimated_duration_seconds

            max_parent_dist = 0.0
            best_parent: str | None = None
            for p in node.parents:
                p_dist = dist.get(p, 0.0)
                if p_dist > max_parent_dist:
                    max_parent_dist = p_dist
                    best_parent = p

            dist[step_name] = max_parent_dist + duration
            pred[step_name] = best_parent

        # Find leaf or step with highest accumulated distance
        best_end = max(dist, key=lambda k: dist[k])

        path: list[str] = []
        curr: str | None = best_end
        while curr is not None:
            path.append(curr)
            curr = pred.get(curr)

        path.reverse()
        return path

    def critical_path_duration(self) -> float:
        """Compute the total estimated duration in seconds of the critical path.

        Returns:
            Summed duration of all steps along the critical path.
        """
        path = self.critical_path()
        return sum(self.nodes[s].estimated_duration_seconds for s in path)

    def roots(self) -> list[GraphNode]:
        """Retrieve all entry-point nodes in the graph with no upstream parents.

        Returns:
            List of root GraphNode instances.
        """
        return [node for node in self.nodes.values() if not node.parents]

    def leaves(self) -> list[GraphNode]:
        """Retrieve all terminal leaf nodes in the graph with no downstream children.

        Returns:
            List of leaf GraphNode instances.
        """
        return [node for node in self.nodes.values() if not node.children]

    def topological_steps(self) -> list[StepDefinition]:
        """Retrieve all StepDefinitions in topological execution order.

        Returns:
            List of StepDefinition instances sorted by dependency order.
        """
        return [self.nodes[name].step_def for name in self.topological_order]


__all__ = [
    "GraphNode",
    "WorkflowGraph",
]
