from __future__ import annotations

from dataclasses import dataclass

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.domain import (
    Direction,
    FindingStatus,
    FrameCandidate,
    FrameSignature,
    FrameVerification,
    MatchChain,
    UnexplainedInterval,
)
from reuse_investigator.signature import (
    hamming_distance,
    mean_absolute_grid_distance,
    normalized_color_distance,
    normalized_cross_correlation,
)


@dataclass(frozen=True, slots=True)
class SourceLibrary:
    """In-memory view of only the compact source signatures.

    Signatures are small fixed-size descriptors (a 64-cell grid plus color
    means), never decoded pixels. Holding descriptors for the library is
    therefore bounded and does not violate the rule against holding decoded
    frames or running all pairwise frame comparisons at once: each probe
    frame is verified against a short, capped candidate list instead.
    """

    clip_ids: tuple[str, ...]
    signatures: dict[str, list[FrameSignature]]


@dataclass(frozen=True, slots=True)
class FrameOutcome:
    """Per-frame audit trail separating retrieval from verification.

    ``candidate_count`` > 0 with ``passed_count`` == 0 is the explicit
    "retrieval hit but verification rejected" condition, which is reported
    differently from a frame that produced no candidate at all.
    """

    probe_frame: int
    candidate_count: int
    passed_count: int
    best_ncc: float
    best_clip_id: str | None
    best_source_frame: int | None
    repeated: bool = False

    @property
    def rejected_candidate(self) -> bool:
        return self.candidate_count > 0 and self.passed_count == 0

    @property
    def no_candidate(self) -> bool:
        return self.candidate_count == 0


class MatchingEngine:
    def __init__(self, settings: InvestigatorSettings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    # Stage 1: coarse retrieval. A hit here is a candidate, never a source.
    # ------------------------------------------------------------------
    def retrieve(
        self,
        probe: FrameSignature,
        library: SourceLibrary,
    ) -> list[FrameCandidate]:
        """Stage-1 coarse retrieval.

        Candidates are admitted on the robust luma-grid MAD gate; Hamming
        distance is retained only as a secondary ordering/diagnostic signal.
        Every returned row is explicitly a *candidate*, never a source.
        """
        candidates: list[FrameCandidate] = []
        mad_gate = self._settings.retrieval_mad_gate
        for clip_id in library.clip_ids:
            for source in library.signatures[clip_id]:
                grid_mad = mean_absolute_grid_distance(probe, source)
                if grid_mad > mad_gate:
                    continue
                color_distance = normalized_color_distance(probe, source)
                candidates.append(
                    FrameCandidate(
                        source_clip_id=clip_id,
                        source_frame=source.frame_index,
                        hamming=hamming_distance(probe.bits, source.bits),
                        grid_mad=grid_mad,
                        color_distance=color_distance,
                    )
                )
        candidates.sort(
            key=lambda item: (item.grid_mad, item.hamming, item.color_distance)
        )
        return candidates[: self._settings.retrieval_candidate_cap]

    # ------------------------------------------------------------------
    # Stage 2: actual verification of one candidate frame pair.
    # ------------------------------------------------------------------
    def verify_pair(
        self,
        probe: FrameSignature,
        candidate: FrameCandidate,
        source: FrameSignature,
    ) -> FrameVerification:
        ncc = normalized_cross_correlation(probe.grid, source.grid)
        color_distance = normalized_color_distance(probe, source)
        flat = (
            probe.grid_variance < self._settings.flat_variance_floor
            or source.grid_variance < self._settings.flat_variance_floor
        )
        color_ok = color_distance <= self._settings.verify_color_gate
        ncc_ok = ncc >= self._settings.verify_ncc_weak
        passed = color_ok and ncc_ok
        if ncc >= self._settings.verify_ncc_strong and color_ok:
            stage = "strong"
        elif passed:
            stage = "weak"
        else:
            stage = "rejected"
        return FrameVerification(
            source_clip_id=candidate.source_clip_id,
            probe_frame=probe.frame_index,
            source_frame=candidate.source_frame,
            ncc=ncc,
            color_distance=color_distance,
            hamming=candidate.hamming,
            flat=flat,
            passed=passed,
            stage=stage,
        )

    def verify_frame(
        self,
        probe: FrameSignature,
        library: SourceLibrary,
    ) -> tuple[list[FrameVerification], list[FrameCandidate]]:
        candidates = self.retrieve(probe, library)
        source_index = {
            clip_id: {sig.frame_index: sig for sig in signatures}
            for clip_id, signatures in library.signatures.items()
        }
        verifications: list[FrameVerification] = []
        for candidate in candidates:
            source = source_index[candidate.source_clip_id][
                candidate.source_frame
            ]
            verifications.append(self.verify_pair(probe, candidate, source))
        verifications.sort(key=lambda item: item.ncc, reverse=True)
        return verifications, candidates

    # ------------------------------------------------------------------
    # Stage 3: per-clip temporal chains + global adjudication.
    # ------------------------------------------------------------------
    def investigate(
        self,
        probe_signatures: list[FrameSignature],
        library: SourceLibrary,
    ) -> tuple[
        list[MatchChain],
        list[UnexplainedInterval],
        dict[int, "FrameOutcome"],
    ]:
        frame_results = self._per_frame_verifications(
            probe_signatures, library
        )
        repeated_frames = self._repeated_probe_frames(probe_signatures)
        per_clip_chains = self._chains_per_clip(
            frame_results, library.clip_ids, repeated_frames
        )
        winners = self._adjudicate(per_clip_chains)
        chains = self._classify(winners)
        outcomes = self._frame_outcomes(frame_results, repeated_frames)
        intervals = self._unexplained(probe_signatures, chains, outcomes)
        return chains, intervals, outcomes

    def _repeated_probe_frames(
        self,
        probe_signatures: list[FrameSignature],
    ) -> set[int]:
        """Mark held/repeated probe frames.

        A frame whose luma grid is effectively identical to its predecessor
        carries no independent temporal motion. Such frames cannot extend a
        unique correspondence, because a held frame matches the same source
        frame repeatedly and would otherwise fabricate forward/backward
        continuity that does not exist.
        """
        ordered = sorted(probe_signatures, key=lambda item: item.frame_index)
        repeated: set[int] = set()
        for previous, current in zip(ordered, ordered[1:]):
            if (
                mean_absolute_grid_distance(previous, current)
                <= self._settings.repeated_frame_mad
            ):
                repeated.add(current.frame_index)
        return repeated

    def _per_frame_verifications(
        self,
        probe_signatures: list[FrameSignature],
        library: SourceLibrary,
    ) -> dict[int, list[FrameVerification]]:
        results: dict[int, list[FrameVerification]] = {}
        for probe in probe_signatures:
            verifications, _candidates = self.verify_frame(probe, library)
            results[probe.frame_index] = verifications
        return results

    def _chains_per_clip(
        self,
        frame_results: dict[int, list[FrameVerification]],
        clip_ids: tuple[str, ...],
        repeated_frames: set[int],
    ) -> dict[str, list["_Chain"]]:
        """Run the temporal DP independently for every source clip.

        Keeping the DP per clip means each clip gets a fair, uncompeted view
        of its own temporal continuity; near-duplicate clips therefore both
        produce a full chain, which is exactly the evidence needed to refuse a
        unique determination.
        """
        return {
            clip_id: self._chains_for_clip(
                clip_id, frame_results, repeated_frames
            )
            for clip_id in clip_ids
        }

    def _chains_for_clip(
        self,
        clip_id: str,
        frame_results: dict[int, list[FrameVerification]],
        repeated_frames: set[int],
    ) -> list["_Chain"]:
        # Strongest passing verification for this clip at each
        # (probe frame -> source frame). Held/repeated probe frames are
        # excluded from chain continuity so they cannot fabricate motion.
        best: dict[tuple[int, int], FrameVerification] = {}
        for verifications in frame_results.values():
            for verification in verifications:
                if (
                    verification.source_clip_id != clip_id
                    or not verification.passed
                    or verification.probe_frame in repeated_frames
                ):
                    continue
                key = (
                    verification.probe_frame,
                    verification.source_frame,
                )
                incumbent = best.get(key)
                if incumbent is None or verification.ncc > incumbent.ncc:
                    best[key] = verification

        start_penalty = 0.35
        # dp[(probe, source)] -> (cumulative score, predecessor key or None).
        dp: dict[tuple[int, int], tuple[float, tuple[int, int] | None]] = {}
        for probe_frame, source_frame in sorted(best):
            verification = best[(probe_frame, source_frame)]
            weight = self._edge_weight(verification)
            forward = (probe_frame - 1, source_frame - 1)
            reverse = (probe_frame - 1, source_frame + 1)
            predecessor = self._predecessor_key(dp, forward, reverse)
            if predecessor is None:
                dp[(probe_frame, source_frame)] = (
                    weight - start_penalty,
                    None,
                )
            else:
                dp[(probe_frame, source_frame)] = (
                    dp[predecessor][0] + weight,
                    predecessor,
                )

        return self._reconstruct_clip_chains(clip_id, best, dp)

    @staticmethod
    def _predecessor_key(
        dp: dict[tuple[int, int], tuple[float, tuple[int, int] | None]],
        forward: tuple[int, int],
        reverse: tuple[int, int],
    ) -> tuple[int, int] | None:
        forward_state = dp.get(forward)
        reverse_state = dp.get(reverse)
        if forward_state is None and reverse_state is None:
            return None
        if reverse_state is None:
            return forward
        if forward_state is None:
            return reverse
        return forward if forward_state[0] >= reverse_state[0] else reverse

    def _reconstruct_clip_chains(
        self,
        clip_id: str,
        best: dict[tuple[int, int], FrameVerification],
        dp: dict[tuple[int, int], tuple[float, tuple[int, int] | None]],
    ) -> list["_Chain"]:
        remaining = set(dp.keys())
        chains: list[_Chain] = []
        while remaining:
            terminal = max(remaining, key=lambda key: dp[key][0])
            path = self._walk_back(terminal, dp)
            chain = self._build_chain(clip_id, [best[key] for key in path])
            if chain is not None:
                chains.append(chain)
            covered = {probe_frame for probe_frame, _source in path}
            remaining = {
                key for key in remaining if key[0] not in covered
            }
        chains.sort(key=lambda item: item.probe_start)
        return chains

    @staticmethod
    def _walk_back(
        terminal: tuple[int, int],
        dp: dict[tuple[int, int], tuple[float, tuple[int, int] | None]],
    ) -> list[tuple[int, int]]:
        path: list[tuple[int, int]] = []
        current: tuple[int, int] | None = terminal
        while current is not None:
            path.append(current)
            current = dp[current][1]
        path.reverse()
        return path

    def _build_chain(
        self,
        clip_id: str,
        verifications: list[FrameVerification],
    ) -> "_Chain | None":
        if not verifications:
            return None
        probe_start = verifications[0].probe_frame
        probe_end = verifications[-1].probe_frame + 1
        source_frames = [item.source_frame for item in verifications]
        direction = Direction.FORWARD.value
        if len(source_frames) >= 2 and source_frames[-1] < source_frames[0]:
            direction = Direction.REVERSED.value
        if direction == Direction.REVERSED.value:
            source_start = max(source_frames)
            source_end = min(source_frames) - 1
        else:
            source_start = min(source_frames)
            source_end = max(source_frames) + 1
        ncc_values = [item.ncc for item in verifications]
        color_values = [item.color_distance for item in verifications]
        flat_frames = sum(1 for item in verifications if item.flat)
        return _Chain(
            clip_id=clip_id,
            probe_start=probe_start,
            probe_end=probe_end,
            source_start=source_start,
            source_end=source_end,
            direction=direction,
            mean_ncc=sum(ncc_values) / len(ncc_values),
            mean_color_distance=sum(color_values) / len(color_values),
            flat_frames=flat_frames,
            verified_frames=len(verifications),
        )

    def _edge_weight(self, verification: FrameVerification) -> float:
        return verification.ncc * (
            1.0 - min(1.0, verification.color_distance)
        )

    def _adjudicate(
        self,
        per_clip_chains: dict[str, list["_Chain"]],
    ) -> list[MatchChain]:
        """Choose one winning chain per probe interval without overlaps.

        All clips' chains are pooled and ranked. A chain can claim a probe
        frame only if no higher-ranked chain already claimed it. Every losing
        clip whose chain overlaps the winner is retained as a named, scored
        alternative instead of being discarded, so tied near-duplicates remain
        visible and force ambiguity.
        """
        pooled = [
            chain
            for chains in per_clip_chains.values()
            for chain in chains
        ]
        if not pooled:
            return []
        ranked = sorted(
            pooled,
            key=lambda chain: (chain.score(), len(chain)),
            reverse=True,
        )
        claimed: set[int] = set()
        winners: list[_Chain] = []
        for chain in ranked:
            interval = set(range(chain.probe_start, chain.probe_end))
            if interval & claimed:
                continue
            claimed |= interval
            winners.append(chain)
        winners.sort(key=lambda item: item.probe_start)

        result: list[MatchChain] = []
        for winner in winners:
            alternatives = self._chain_alternatives(winner, per_clip_chains)
            result.append(
                MatchChain(
                    source_clip_id=winner.clip_id,
                    probe_start=winner.probe_start,
                    probe_end=winner.probe_end,
                    source_start=winner.source_start,
                    source_end=winner.source_end,
                    direction=winner.direction,
                    mean_ncc=winner.mean_ncc,
                    mean_color_distance=winner.mean_color_distance,
                    flat_frames=winner.flat_frames,
                    verified_frames=winner.verified_frames,
                    status=FindingStatus.AMBIGUOUS.value,
                    score=winner.score(),
                    alternatives=tuple(alternatives),
                )
            )
        return result

    def _chain_alternatives(
        self,
        winner: "_Chain",
        per_clip_chains: dict[str, list["_Chain"]],
    ) -> list[dict[str, object]]:
        """Rank other clips' best overlapping chain against the winner."""
        winner_interval = set(
            range(winner.probe_start, winner.probe_end)
        )
        alternatives: list[dict[str, object]] = []
        for clip_id, chains in per_clip_chains.items():
            if clip_id == winner.clip_id:
                continue
            overlapping = [
                chain
                for chain in chains
                if set(range(chain.probe_start, chain.probe_end))
                & winner_interval
            ]
            if not overlapping:
                continue
            best_alt = max(overlapping, key=lambda chain: chain.score())
            shared = len(
                set(range(best_alt.probe_start, best_alt.probe_end))
                & winner_interval
            )
            alternatives.append(
                {
                    "source_clip_id": clip_id,
                    "mean_ncc": round(best_alt.mean_ncc, 4),
                    "matching_frames": shared,
                    "probe_range": [
                        best_alt.probe_start,
                        best_alt.probe_end,
                    ],
                    "source_range": [
                        best_alt.source_start,
                        best_alt.source_end,
                    ],
                    "direction": best_alt.direction,
                }
            )
        alternatives.sort(key=lambda item: item["mean_ncc"], reverse=True)
        return alternatives[:4]

    def _classify(self, chains: list[MatchChain]) -> list[MatchChain]:
        classified: list[MatchChain] = []
        for chain in chains:
            length = chain.probe_end - chain.probe_start
            too_short = length < self._settings.min_chain_frames
            mostly_flat = chain.flat_frames > length // 2
            strong = chain.mean_ncc >= self._settings.verify_ncc_strong
            unique = self._is_unique(chain)
            if too_short or mostly_flat or not strong or not unique:
                status = FindingStatus.AMBIGUOUS.value
            else:
                status = FindingStatus.CONFIRMED.value
            classified.append(
                MatchChain(
                    source_clip_id=chain.source_clip_id,
                    probe_start=chain.probe_start,
                    probe_end=chain.probe_end,
                    source_start=chain.source_start,
                    source_end=chain.source_end,
                    direction=chain.direction,
                    mean_ncc=chain.mean_ncc,
                    mean_color_distance=chain.mean_color_distance,
                    flat_frames=chain.flat_frames,
                    verified_frames=chain.verified_frames,
                    status=status,
                    score=chain.score,
                    alternatives=chain.alternatives,
                )
            )
        return classified

    def _is_unique(self, chain: MatchChain) -> bool:
        if not chain.alternatives:
            return True
        runner_up = float(chain.alternatives[0]["mean_ncc"])
        return (
            chain.mean_ncc - runner_up
        ) >= self._settings.uniqueness_margin

    def _frame_outcomes(
        self,
        frame_results: dict[int, list[FrameVerification]],
        repeated_frames: set[int] | None = None,
    ) -> dict[int, FrameOutcome]:
        repeated_frames = repeated_frames or set()
        outcomes: dict[int, FrameOutcome] = {}
        for probe_frame, verifications in frame_results.items():
            candidate_count = len(verifications)
            passing = [v for v in verifications if v.passed]
            best = verifications[0] if verifications else None
            outcomes[probe_frame] = FrameOutcome(
                probe_frame=probe_frame,
                candidate_count=candidate_count,
                passed_count=len(passing),
                best_ncc=round(best.ncc, 4) if best else 0.0,
                best_clip_id=best.source_clip_id if best else None,
                best_source_frame=best.source_frame if best else None,
                repeated=probe_frame in repeated_frames,
            )
        return outcomes

    def _unexplained(
        self,
        probe_signatures: list[FrameSignature],
        chains: list[MatchChain],
        outcomes: dict[int, FrameOutcome] | None = None,
    ) -> list[UnexplainedInterval]:
        if not probe_signatures:
            return []
        total = probe_signatures[-1].frame_index + 1
        covered = [False] * total
        for chain in chains:
            for frame in range(chain.probe_start, chain.probe_end):
                covered[frame] = True
        intervals: list[UnexplainedInterval] = []
        cursor = 0
        while cursor < total:
            if covered[cursor]:
                cursor += 1
                continue
            start = cursor
            while cursor < total and not covered[cursor]:
                cursor += 1
            intervals: list[UnexplainedInterval] = []
        cursor = 0
        while cursor < total:
            if covered[cursor]:
                cursor += 1
                continue
            start = cursor
            current_reason = self._frame_reason(cursor, outcomes or {})
            cursor += 1
            while cursor < total and not covered[cursor]:
                next_reason = self._frame_reason(cursor, outcomes or {})
                if next_reason != current_reason:
                    break
                cursor += 1
            intervals.append(
                UnexplainedInterval(
                    probe_start=start,
                    probe_end=cursor,
                    reason=current_reason,
                )
            )
        return intervals

    @staticmethod
    def _frame_reason(
        frame: int,
        outcomes: dict[int, FrameOutcome],
    ) -> str:
        outcome = outcomes.get(frame)
        if outcome is not None and outcome.rejected_candidate:
            return (
                "retrieval returned candidates but every pair failed NCC or "
                "color verification; the hit is rejected and no source is claimed"
            )
        if outcome is not None and outcome.no_candidate:
            return (
                "no retrieval candidate within the similarity gate; the footage "
                "is retained as genuinely unexplained rather than guessed"
            )
        return (
            "no verified source could be established for this frame; retained "
            "as unexplained rather than guessed"
        )


@dataclass(frozen=True, slots=True)
class _Chain:
    clip_id: str
    probe_start: int
    probe_end: int
    source_start: int
    source_end: int
    direction: str
    mean_ncc: float
    mean_color_distance: float
    flat_frames: int
    verified_frames: int

    def __len__(self) -> int:
        return self.probe_end - self.probe_start

    def score(self) -> float:
        # Coherent long runs outrank short fragments; within the same length a
        # high-NCC, color-consistent chain wins. The length term is small enough
        # never to override a clearly stronger correspondence.
        return (
            self.mean_ncc * (1.0 - min(1.0, self.mean_color_distance))
            + 0.03 * len(self)
        )
