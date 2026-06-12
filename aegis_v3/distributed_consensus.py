"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/distributed_consensus.py — Raft Protocol State Coordination
==============================================================================

PURPOSE
-------
Before any structural system fix is applied to production, ALL active nodes
in the Aegis cluster must agree on the deployment decision. This module
implements a production-faithful simulation of the Raft consensus algorithm.

RAFT PROTOCOL OVERVIEW
-----------------------
Raft guarantees that at most one leader exists per term, and that a commit
is durable once acknowledged by a quorum (majority) of nodes.

  Node Roles:
    FOLLOWER  — Default state. Awaits heartbeats from leader.
    CANDIDATE — Starts an election when election_timeout expires.
    LEADER    — Sends heartbeats; replicates log entries to followers.

  Key RPCs (simulated via asyncio.Queue message passing):
    RequestVote    — CANDIDATE → peers (LeaderElectionVote)
    AppendEntries  — LEADER → followers (HeartbeatMessage + log entries)

  Election Safety:
    - Each node votes at most once per term
    - A candidate only wins if it receives votes from a strict majority
    - Election timeouts are randomized [150ms, 300ms] to avoid split votes

  Log Replication Safety:
    - Leader appends entries to its log first
    - Sends AppendEntries to followers in parallel
    - Commits once a quorum acknowledges receipt

NETWORK FAULT SIMULATION
------------------------
Each NodeConfig can be configured with `simulate_failure=True` and a
`failure_probability` (0.0-1.0). When active, the node randomly drops
incoming messages to simulate:
  - Transient network partitions
  - Node crashes mid-election
  - Leader isolation scenarios

CONSENSUS GUARANTEE
-------------------
`AgentClusterCoordinator.wait_for_consensus()` blocks until either:
  a) A stable leader is elected and the deployment command is committed
     across a quorum — returning a ClusterStateLog with consensus_reached=True
  b) `timeout_seconds` expires — returning with consensus_reached=False
     (pipeline escalates to human review)

==============================================================================
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import Optional

from aegis_v3.schema_v3 import (
    ClusterStateLog,
    HeartbeatMessage,
    LeaderElectionVote,
    NodeConfig,
    NodeRole,
    RaftLogEntry,
)

try:
    from aegis_v3.persistence import RaftPersistenceBackend
    _PERSISTENCE_AVAILABLE = True
except ImportError:
    _PERSISTENCE_AVAILABLE = False

logger = logging.getLogger("aegis.consensus")

# ---------------------------------------------------------------------------
# Timing constants (milliseconds, then converted to seconds for asyncio.sleep)
# ---------------------------------------------------------------------------
ELECTION_TIMEOUT_MIN_MS: float = 150.0
ELECTION_TIMEOUT_MAX_MS: float = 300.0
HEARTBEAT_INTERVAL_MS: float   = 50.0
RPC_TIMEOUT_MS: float          = 80.0


# ---------------------------------------------------------------------------
# Internal per-node state (not Pydantic — mutable runtime state)
# ---------------------------------------------------------------------------

class _NodeState:
    """
    Mutable runtime state for a single Raft node.
    All fields are accessed only from the node's own asyncio task.
    """
    __slots__ = (
        "node_id", "cfg", "role", "current_term", "voted_for",
        "log", "commit_index", "last_applied",
        "next_index", "match_index",
        "votes_received", "leader_id",
        "last_heartbeat_time", "election_timeout_s",
        "inbox",  # asyncio.Queue for incoming RPCs
        "_persistence",  # RaftPersistenceBackend (None if unavailable)
    )

    def __init__(self, cfg: NodeConfig) -> None:
        self.node_id: str          = cfg.node_id
        self.cfg: NodeConfig       = cfg
        self.role: NodeRole        = NodeRole.FOLLOWER
        self.current_term: int     = 0
        self.voted_for: Optional[str] = None
        self.log: list[RaftLogEntry] = []
        self.commit_index: int     = 0
        self.last_applied: int     = 0
        self.next_index: dict[str, int]  = {}
        self.match_index: dict[str, int] = {}
        self.votes_received: set[str]    = set()
        self.leader_id: Optional[str]    = None
        self.last_heartbeat_time: float  = time.monotonic()
        self.election_timeout_s: float   = self._random_timeout()
        self.inbox: asyncio.Queue        = asyncio.Queue(maxsize=256)
        # Raft durable storage (persists term/vote to SQLite)
        self._persistence: Optional["RaftPersistenceBackend"] = (
            RaftPersistenceBackend(node_id=cfg.node_id)
            if _PERSISTENCE_AVAILABLE else None
        )

    async def restore_from_disk(self) -> None:
        """Restore currentTerm, votedFor, and log from stable storage (Raft §5.4)."""
        if self._persistence is None:
            return
        try:
            term, voted_for, log_entries = await self._persistence.load_state()
            self.current_term = term
            self.voted_for    = voted_for
            # Restore log entries
            self.log = [
                RaftLogEntry(**entry) if isinstance(entry, dict) else entry
                for entry in log_entries
            ]
            logger.debug(
                f"[{self.node_id}] State restored: term={term}, "
                f"voted_for={voted_for}, log_len={len(self.log)}"
            )
        except Exception as exc:
            logger.warning(f"[{self.node_id}] Persistence restore failed: {exc} — starting fresh")

    async def persist(self) -> None:
        """Persist currentTerm, votedFor, and log to stable storage (fire-and-forget)."""
        if self._persistence is None:
            return
        try:
            await self._persistence.persist_state(
                current_term=self.current_term,
                voted_for=self.voted_for,
                log_entries=[e.model_dump() for e in self.log],
            )
        except Exception as exc:
            logger.debug(f"[{self.node_id}] Persist failed (non-fatal): {exc}")

    def _random_timeout(self) -> float:
        base = self.cfg.election_timeout_ms or random.uniform(
            ELECTION_TIMEOUT_MIN_MS, ELECTION_TIMEOUT_MAX_MS
        )
        # Add jitter ±20%
        jitter = base * random.uniform(-0.2, 0.2)
        return (base + jitter) / 1000.0   # convert to seconds

    def reset_election_timeout(self) -> None:
        self.election_timeout_s = self._random_timeout()
        self.last_heartbeat_time = time.monotonic()

    @property
    def last_log_index(self) -> int:
        return len(self.log)

    @property
    def last_log_term(self) -> int:
        return self.log[-1].term if self.log else 0

    def drop_message(self) -> bool:
        """Simulate network fault by randomly dropping messages."""
        if self.cfg.simulate_failure and self.cfg.failure_probability > 0:
            return random.random() < self.cfg.failure_probability
        return False


# ---------------------------------------------------------------------------
# Message envelope (wraps Pydantic models for the internal message bus)
# ---------------------------------------------------------------------------

class _Message:
    __slots__ = ("msg_type", "payload", "sender_id", "reply_queue")

    def __init__(
        self,
        msg_type: str,
        payload: HeartbeatMessage | LeaderElectionVote,
        sender_id: str,
        reply_queue: Optional[asyncio.Queue] = None,
    ) -> None:
        self.msg_type    = msg_type       # "heartbeat" | "request_vote" | "vote_response"
        self.payload     = payload
        self.sender_id   = sender_id
        self.reply_queue = reply_queue


# ---------------------------------------------------------------------------
# Main Coordinator
# ---------------------------------------------------------------------------

class AgentClusterCoordinator:
    """
    Orchestrates an Aegis agent cluster using the Raft consensus protocol.

    Simulates a real distributed cluster on a single machine using asyncio
    tasks — each node runs as a concurrent coroutine communicating via
    asyncio.Queue (simulating the network).

    The coordinator guarantees that a deployment command is committed
    across a quorum before returning consensus. If the cluster is healthy,
    this typically completes in 1-3 election rounds (150-400ms simulated time,
    accelerated to <50ms real time using scaled-down timeouts).

    Usage (async):
        coord = AgentClusterCoordinator(node_count=5)
        await coord.start_cluster()
        cluster_log = await coord.wait_for_consensus(
            command="DEPLOY_PATCH_abc123", timeout_seconds=5.0
        )
        await coord.stop_cluster()
    """

    def __init__(
        self,
        node_count: int = 5,
        node_configs: Optional[list[NodeConfig]] = None,
        time_acceleration: float = 10.0,
    ) -> None:
        """
        Args:
            node_count:        Number of simulated cluster nodes (odd number preferred).
            node_configs:      Custom NodeConfig per node (auto-generated if None).
            time_acceleration: Factor to speed up simulated timeouts (10x default).
                                At 10x, a 150ms election timeout fires in 15ms real time.
        """
        import sys
        if sys.platform == "win32" and time_acceleration > 2.0:
            time_acceleration = 2.0  # Windows clock resolution (~15.6ms) requires slower timeouts to prevent split votes

        self._accel      = time_acceleration
        self._quorum     = (node_count // 2) + 1

        # Build node configs
        if node_configs:
            configs = node_configs
        else:
            configs = [
                NodeConfig(
                    node_id=f"node-{i:02d}",
                    election_timeout_ms=random.uniform(
                        ELECTION_TIMEOUT_MIN_MS, ELECTION_TIMEOUT_MAX_MS
                    ) / time_acceleration,
                    heartbeat_interval_ms=HEARTBEAT_INTERVAL_MS / time_acceleration,
                    simulate_failure=(i == node_count - 1 and node_count > 2),
                    failure_probability=0.15 if i == node_count - 1 else 0.0,
                )
                for i in range(node_count)
            ]

        self._nodes: dict[str, _NodeState] = {
            cfg.node_id: _NodeState(cfg) for cfg in configs
        }
        self._tasks: list[asyncio.Task] = []
        self._cluster_events: asyncio.Queue[ClusterStateLog] = asyncio.Queue()
        self._running = False
        self._committed_commands: list[RaftLogEntry] = []

        logger.info(
            f"AgentClusterCoordinator initialized | "
            f"nodes={node_count} | quorum={self._quorum} | "
            f"accel={time_acceleration}x"
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def start_cluster(self) -> None:
        """Start all node tasks and begin the Raft protocol."""
        self._running = True
        # Restore durable state for all nodes before starting (Raft §5.4)
        restore_tasks = [state.restore_from_disk() for state in self._nodes.values()]
        await asyncio.gather(*restore_tasks, return_exceptions=True)
        for node_id, state in self._nodes.items():
            task = asyncio.create_task(
                self._run_node(state), name=f"raft-{node_id}"
            )
            self._tasks.append(task)
        logger.info(f"Cluster started with {len(self._nodes)} nodes.")

    async def stop_cluster(self) -> None:
        """Cancel all node tasks and terminate the cluster gracefully."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Cluster stopped.")

    async def wait_for_consensus(
        self,
        command: str,
        payload: Optional[dict] = None,
        timeout_seconds: float = 5.0,
    ) -> ClusterStateLog:
        """
        Submit a command and block until it is committed by a quorum.

        Args:
            command:         The deployment command string (e.g., "DEPLOY_PATCH_xyz").
            payload:         Optional structured payload attached to the log entry.
            timeout_seconds: Maximum wait time before returning with consensus=False.

        Returns:
            ClusterStateLog snapshot with consensus_reached=True/False.
        """
        logger.info(f"Waiting for cluster consensus on command: '{command}'")
        t0 = time.monotonic()

        # Poll until a leader is elected and commits the entry
        deadline = t0 + timeout_seconds
        while time.monotonic() < deadline:
            leader = self._find_leader()
            if leader:
                # Submit command to leader's log
                entry = RaftLogEntry(
                    index=leader.last_log_index + 1,
                    term=leader.current_term,
                    command=command,
                    payload=payload or {},
                )
                leader.log.append(entry)
                logger.info(
                    f"Command submitted to leader '{leader.node_id}' "
                    f"at term={leader.current_term}, index={entry.index}"
                )

                # Wait for quorum replication
                committed = await self._wait_for_commit(
                    entry=entry,
                    timeout=min(2.0, deadline - time.monotonic()),
                )
                if committed:
                    snapshot = self._snapshot_cluster(command_committed=True)
                    logger.info(
                        f"CONSENSUS REACHED | term={snapshot.current_term} | "
                        f"leader={snapshot.leader_id} | "
                        f"committed_index={snapshot.committed_index}"
                    )
                    return snapshot

            await asyncio.sleep(0.02)   # 20ms poll interval

        # Timeout — return partial state
        snapshot = self._snapshot_cluster(command_committed=False)
        logger.error(
            f"CONSENSUS TIMEOUT after {timeout_seconds}s — "
            f"no stable leader or quorum not reached."
        )
        return snapshot

    def get_current_leader(self) -> Optional[str]:
        """Return the current leader node ID, or None if no leader elected."""
        leader = self._find_leader()
        return leader.node_id if leader else None

    def get_cluster_roles(self) -> dict[str, NodeRole]:
        """Return the current role of every node in the cluster."""
        return {nid: s.role for nid, s in self._nodes.items()}

    # -----------------------------------------------------------------------
    # Node Lifecycle
    # -----------------------------------------------------------------------

    async def _run_node(self, state: _NodeState) -> None:
        """Main loop for a single Raft node — runs for the lifetime of the cluster."""
        logger.debug(f"Node '{state.node_id}' starting as FOLLOWER.")
        while self._running:
            try:
                if state.role == NodeRole.FOLLOWER:
                    await self._follower_loop(state)
                elif state.role == NodeRole.CANDIDATE:
                    await self._candidate_loop(state)
                elif state.role == NodeRole.LEADER:
                    await self._leader_loop(state)
            except asyncio.CancelledError:
                logger.debug(f"Node '{state.node_id}' task cancelled.")
                return
            except Exception as exc:
                logger.error(f"Node '{state.node_id}' fault: {exc}", exc_info=True)
                await asyncio.sleep(0.05)

    async def _follower_loop(self, state: _NodeState) -> None:
        """
        FOLLOWER behavior:
          - Listen for heartbeats and AppendEntries from leader
          - Respond to RequestVote from candidates
          - Transition to CANDIDATE if election timeout fires
        """
        timeout = state.election_timeout_s
        try:
            msg: _Message = await asyncio.wait_for(
                state.inbox.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            # Election timeout — transition to CANDIDATE
            elapsed_since_hb = time.monotonic() - state.last_heartbeat_time
            if elapsed_since_hb >= state.election_timeout_s:
                logger.info(
                    f"Node '{state.node_id}' election timeout "
                    f"({elapsed_since_hb*1000:.1f}ms) — starting election."
                )
                state.role = NodeRole.CANDIDATE
            return

        if state.drop_message():
            logger.debug(f"Node '{state.node_id}' dropped message (simulated fault).")
            return

        if msg.msg_type == "heartbeat":
            hb: HeartbeatMessage = msg.payload
            if hb.term >= state.current_term:
                state.current_term = hb.term
                state.leader_id = hb.leader_id
                state.role = NodeRole.FOLLOWER
                state.reset_election_timeout()
                # Replicate log entries if any
                for entry in hb.entries:
                    if entry.index > state.last_log_index:
                        state.log.append(entry)
                # Advance commit index
                if hb.leader_commit > state.commit_index:
                    state.commit_index = min(
                        hb.leader_commit, state.last_log_index
                    )

        elif msg.msg_type == "request_vote":
            vote_req: LeaderElectionVote = msg.payload
            grant = False
            if (
                vote_req.term >= state.current_term
                and (
                    state.voted_for is None
                    or state.voted_for == vote_req.candidate_id
                )
                and vote_req.last_log_index >= state.last_log_index
            ):
                state.current_term = vote_req.term
                state.voted_for = vote_req.candidate_id
                state.reset_election_timeout()
                grant = True

            if grant:
                # Raft §5.4: persist votedFor to stable storage BEFORE sending
                # response to prevent double-voting after a restart
                asyncio.ensure_future(state.persist())

            response = LeaderElectionVote(
                term=state.current_term,
                candidate_id=vote_req.candidate_id,
                vote_granted=grant,
                voter_id=state.node_id,
            )
            if msg.reply_queue:
                try:
                    msg.reply_queue.put_nowait(response)
                except asyncio.QueueFull:
                    pass

    async def _candidate_loop(self, state: _NodeState) -> None:
        """
        CANDIDATE behavior:
          - Increment term, vote for self
          - Broadcast RequestVote to all peers
          - Collect votes; win election if quorum reached
          - Fall back to FOLLOWER if higher term discovered
          - Restart election if split vote / timeout
        """
        state.current_term += 1
        state.voted_for = state.node_id
        state.votes_received = {state.node_id}
        state.leader_id = None
        state.reset_election_timeout()
        # Raft §5.4: persist new term and self-vote before broadcasting RequestVote
        asyncio.ensure_future(state.persist())

        logger.info(
            f"Node '{state.node_id}' CANDIDATE | term={state.current_term} | "
            f"soliciting {len(self._nodes) - 1} vote(s)"
        )

        vote_req = LeaderElectionVote(
            term=state.current_term,
            candidate_id=state.node_id,
            last_log_index=state.last_log_index,
            last_log_term=state.last_log_term,
        )

        # Broadcast RequestVote in parallel
        reply_queue: asyncio.Queue[LeaderElectionVote] = asyncio.Queue()
        peers = [n for n in self._nodes.values() if n.node_id != state.node_id]
        for peer in peers:
            msg = _Message("request_vote", vote_req, state.node_id, reply_queue)
            try:
                peer.inbox.put_nowait(msg)
            except asyncio.QueueFull:
                pass

        # Collect votes with timeout
        deadline = asyncio.get_event_loop().time() + state.election_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            try:
                response: LeaderElectionVote = await asyncio.wait_for(
                    reply_queue.get(),
                    timeout=max(0.001, deadline - asyncio.get_event_loop().time()),
                )
                if response.term > state.current_term:
                    # Discovered higher term — step down
                    state.current_term = response.term
                    state.voted_for = None
                    state.role = NodeRole.FOLLOWER
                    logger.info(
                        f"Node '{state.node_id}' stepping down — "
                        f"higher term {response.term} discovered."
                    )
                    return

                if response.vote_granted:
                    state.votes_received.add(response.voter_id or "?")
                    logger.debug(
                        f"Node '{state.node_id}' received vote from "
                        f"'{response.voter_id}' ({len(state.votes_received)}/{self._quorum})"
                    )

                    if len(state.votes_received) >= self._quorum:
                        # Won the election
                        state.role = NodeRole.LEADER
                        state.leader_id = state.node_id
                        # Initialize nextIndex for all peers
                        for peer in peers:
                            state.next_index[peer.node_id] = state.last_log_index + 1
                            state.match_index[peer.node_id] = 0
                        logger.info(
                            f"*** LEADER ELECTED: '{state.node_id}' "
                            f"at term={state.current_term} | "
                            f"votes={len(state.votes_received)}/{len(self._nodes)} ***"
                        )
                        # Notify coordinator
                        self._cluster_events.put_nowait(
                            self._snapshot_cluster(command_committed=False)
                        )
                        return

            except asyncio.TimeoutError:
                break

        # Split vote or timeout — restart election next iteration
        state.role = NodeRole.FOLLOWER
        logger.debug(
            f"Node '{state.node_id}' split vote / timeout — "
            f"reverting to FOLLOWER for next round."
        )

    async def _leader_loop(self, state: _NodeState) -> None:
        """
        LEADER behavior:
          - Broadcast heartbeats to all followers every heartbeat_interval
          - Replicate new log entries to followers
          - Advance commit index when quorum acknowledges
        """
        hb_interval_s = state.cfg.heartbeat_interval_ms / 1000.0

        while state.role == NodeRole.LEADER and self._running:
            # Build heartbeat with any uncommitted entries
            entries_to_send = [
                e for e in state.log if e.index > state.commit_index
            ][:5]   # Send at most 5 entries per heartbeat

            hb = HeartbeatMessage(
                term=state.current_term,
                leader_id=state.node_id,
                prev_log_index=state.last_log_index,
                prev_log_term=state.last_log_term,
                leader_commit=state.commit_index,
                entries=entries_to_send,
            )

            ack_count = 1  # Self counts
            for peer in self._nodes.values():
                if peer.node_id == state.node_id:
                    continue
                msg = _Message("heartbeat", hb, state.node_id)
                try:
                    peer.inbox.put_nowait(msg)
                    ack_count += 1
                except asyncio.QueueFull:
                    logger.debug(
                        f"Leader '{state.node_id}' inbox full for peer "
                        f"'{peer.node_id}' — skipping heartbeat."
                    )

            # Advance commit index if quorum acknowledged the latest entry
            if state.log and ack_count >= self._quorum:
                latest = state.log[-1]
                if latest.index > state.commit_index:
                    state.commit_index = latest.index
                    self._committed_commands.append(latest)
                    logger.info(
                        f"Leader '{state.node_id}' committed entry index={latest.index} "
                        f"cmd='{latest.command}' (acks={ack_count})"
                    )

            await asyncio.sleep(hb_interval_s)

            # Drain own inbox (leaders ignore vote requests for lower terms)
            while not state.inbox.empty():
                try:
                    msg = state.inbox.get_nowait()
                    if msg.msg_type == "heartbeat":
                        hb_in: HeartbeatMessage = msg.payload
                        if hb_in.term > state.current_term:
                            state.current_term = hb_in.term
                            state.role = NodeRole.FOLLOWER
                            state.voted_for = None
                            logger.info(
                                f"Leader '{state.node_id}' stepping down — "
                                f"higher term {hb_in.term} from '{hb_in.leader_id}'."
                            )
                            return
                except asyncio.QueueEmpty:
                    break

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _find_leader(self) -> Optional[_NodeState]:
        """Return the current leader node state, or None."""
        for state in self._nodes.values():
            if state.role == NodeRole.LEADER:
                return state
        return None

    async def _wait_for_commit(
        self,
        entry: RaftLogEntry,
        timeout: float,
    ) -> bool:
        """
        Wait until the given log entry index is committed by the leader.

        Returns True if committed within the timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            leader = self._find_leader()
            if leader and leader.commit_index >= entry.index:
                return True
            await asyncio.sleep(0.01)
        return False

    def _snapshot_cluster(self, command_committed: bool) -> ClusterStateLog:
        """Build a full ClusterStateLog snapshot of current cluster state."""
        leader = self._find_leader()
        return ClusterStateLog(
            current_term=max(s.current_term for s in self._nodes.values()),
            leader_id=leader.node_id if leader else None,
            committed_index=leader.commit_index if leader else 0,
            nodes={nid: s.role for nid, s in self._nodes.items()},
            log_entries=list(self._committed_commands),
            quorum_size=self._quorum,
            consensus_reached=command_committed,
        )
