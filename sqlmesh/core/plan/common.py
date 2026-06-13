from __future__ import annotations
import typing as t
import logging
import re
from enum import Enum
from dataclasses import dataclass, field

from sqlglot.optimizer.simplify import gen

from sqlmesh.core.state_sync import StateReader
from sqlmesh.core.snapshot import Snapshot, SnapshotId, SnapshotIdAndVersion, SnapshotNameVersion
from sqlmesh.core.snapshot.definition import Interval
from sqlmesh.utils.dag import DAG
from sqlmesh.utils.pydantic import PydanticModel
from sqlmesh.utils.date import now_timestamp

logger = logging.getLogger(__name__)


def should_force_rebuild(old: Snapshot, new: Snapshot) -> bool:
    if new.is_view and new.is_indirect_non_breaking and not new.is_forward_only:
        # View models always need to be rebuilt to reflect updated upstream dependencies
        return True
    if new.is_seed and not (
        new.is_metadata
        and new.previous_version
        and new.previous_version.snapshot_id(new.name) == old.snapshot_id
    ):
        # Seed models always need to be rebuilt to reflect changes in the seed file
        # Unless only their metadata has been updated (eg description added) and the seed file has not been touched
        return True
    recreation_request = physical_recreation_request(old, new)
    if recreation_request and recreation_request.clear_direct_intervals:
        return True
    return is_breaking_kind_change(old, new)


def is_breaking_kind_change(old: Snapshot, new: Snapshot) -> bool:
    if new.is_model != old.is_model:
        # If one is a model and the other isn't, then we need to rebuild
        return True
    if not new.is_model or not old.is_model:
        # If neither are models, then we don't need to rebuild
        # Note that the remaining checks only apply to model snapshots
        return False
    if old.virtual_environment_mode != new.virtual_environment_mode:
        # If the virtual environment mode has changed, then we need to rebuild
        return True
    if old.model.kind.name == new.model.kind.name:
        # If the kind hasn't changed, then we don't need to rebuild
        return False
    if not old.is_incremental or not new.is_incremental:
        # If either is not incremental, then we need to rebuild
        return True
    if old.model.partitioned_by == new.model.partitioned_by:
        # If the partitioning hasn't changed, then we don't need to rebuild
        return False
    return True


_PHYSICAL_PROPERTY_NONE = "none"
_PHYSICAL_PROPERTY_SEMANTIC = "semantic"
_PHYSICAL_PROPERTY_LAYOUT = "layout"
_PHYSICAL_PROPERTY_UNKNOWN = "unknown"

class PhysicalRecreationReason(str, Enum):
    DORIS_SEMANTIC_KEY = "doris_semantic_key"
    DORIS_SEMANTIC_PARTITION = "doris_semantic_partition"
    DORIS_LAYOUT_DISTRIBUTION = "doris_layout_distribution"
    DORIS_LAYOUT_STORAGE = "doris_layout_storage"
    DORIS_UNKNOWN_PROPERTY = "doris_unknown_property"


class PhysicalRecreationRequest(PydanticModel, frozen=True):
    snapshot_id: SnapshotId
    reason: PhysicalRecreationReason
    skip_schema_migration: bool = True
    recreate_direct_table: bool = True
    clear_direct_intervals: bool = True
    requires_full_backfill: bool = True
    propagates_downstream: bool = False
    breaking: bool = False


_DORIS_KEY_PHYSICAL_PROPERTIES = {
    "unique_key",
    "duplicate_key",
}
_DORIS_DISTRIBUTION_PHYSICAL_PROPERTIES = {
    "distributed_by",
    "distribution",
    "distribution_key",
    "bucket",
    "buckets",
    "bucket_count",
}
_DORIS_STORAGE_PHYSICAL_PROPERTIES = {
    "replication_num",
    "replication",
    "replication_allocation",
    "storage_policy",
    "compression",
    "codec",
    "bloom_filter_columns",
    "bloom_filter",
    "index",
    "indexes",
}
_DORIS_RETENTION_PHYSICAL_PROPERTIES = {
    "ttl",
    "retention",
    "retention_period",
    "retention_seconds",
    "retention_days",
}


def physical_recreation_request(
    old: Snapshot, new: Snapshot
) -> t.Optional[PhysicalRecreationRequest]:
    """Returns a reason-aware request for recreating a Doris physical table."""
    if not new.is_model or not old.is_model:
        return None
    if new.model.dialect != "doris" or old.model.dialect != "doris":
        return None
    if not new.model.kind.is_materialized:
        return None

    changed_properties = _changed_physical_properties(new, old)
    partitioning_changed = old.model.partitioned_by != new.model.partitioned_by
    if not changed_properties and not partitioning_changed:
        return None

    reason = _doris_physical_recreation_reason(changed_properties, partitioning_changed)
    propagates_downstream = reason in {
        PhysicalRecreationReason.DORIS_SEMANTIC_KEY,
        PhysicalRecreationReason.DORIS_SEMANTIC_PARTITION,
        PhysicalRecreationReason.DORIS_UNKNOWN_PROPERTY,
    }
    return PhysicalRecreationRequest(
        snapshot_id=new.snapshot_id,
        reason=reason,
        propagates_downstream=propagates_downstream,
        breaking=propagates_downstream,
    )


def requires_physical_recreation(old: Snapshot, new: Snapshot) -> bool:
    """Returns whether a physical property change requires a new physical table."""
    return physical_recreation_request(old, new) is not None


def _doris_physical_recreation_reason(
    changed_properties: t.Set[str], partitioning_changed: bool
) -> PhysicalRecreationReason:
    if any(not _is_known_doris_recreation_property(key) for key in changed_properties):
        return PhysicalRecreationReason.DORIS_UNKNOWN_PROPERTY
    if changed_properties & _DORIS_KEY_PHYSICAL_PROPERTIES:
        return PhysicalRecreationReason.DORIS_SEMANTIC_KEY
    if partitioning_changed or any(_is_doris_partition_property(key) for key in changed_properties):
        return PhysicalRecreationReason.DORIS_SEMANTIC_PARTITION
    if changed_properties & _DORIS_DISTRIBUTION_PHYSICAL_PROPERTIES:
        return PhysicalRecreationReason.DORIS_LAYOUT_DISTRIBUTION
    if changed_properties & _DORIS_STORAGE_PHYSICAL_PROPERTIES:
        return PhysicalRecreationReason.DORIS_LAYOUT_STORAGE
    return PhysicalRecreationReason.DORIS_UNKNOWN_PROPERTY


def _is_known_doris_recreation_property(key: str) -> bool:
    return (
        key in _DORIS_KEY_PHYSICAL_PROPERTIES
        or _is_doris_partition_property(key)
        or key in _DORIS_DISTRIBUTION_PHYSICAL_PROPERTIES
        or key in _DORIS_STORAGE_PHYSICAL_PROPERTIES
    )


def _is_doris_partition_property(key: str) -> bool:
    return (
        "partition" in key
        or key in _DORIS_RETENTION_PHYSICAL_PROPERTIES
        or key.startswith("retention_")
    )






def _physical_property_change_category(new: Snapshot, old: Snapshot) -> str:
    changed_properties = _changed_physical_properties(new, old)
    if new.model.dialect == "doris" and old.model.partitioned_by != new.model.partitioned_by:
        return _PHYSICAL_PROPERTY_SEMANTIC
    if not changed_properties:
        return _PHYSICAL_PROPERTY_NONE

    categories = {_physical_property_category(key) for key in changed_properties}
    if _PHYSICAL_PROPERTY_SEMANTIC in categories:
        return _PHYSICAL_PROPERTY_SEMANTIC
    if _PHYSICAL_PROPERTY_UNKNOWN in categories:
        return _PHYSICAL_PROPERTY_UNKNOWN
    return _PHYSICAL_PROPERTY_LAYOUT


def _serialized_physical_properties(snapshot: Snapshot) -> t.Dict[str, str]:
    return {
        _normalize_physical_property_key(key): gen(value)
        for key, value in snapshot.model.physical_properties.items()
    }


def _changed_physical_properties(new: Snapshot, old: Snapshot) -> t.Set[str]:
    old_properties = _serialized_physical_properties(old)
    new_properties = _serialized_physical_properties(new)
    return {
        key
        for key in old_properties.keys() | new_properties.keys()
        if old_properties.get(key) != new_properties.get(key)
    }


def _physical_property_category(key: str) -> str:
    if key in {
        "unique_key",
        "primary_key",
        "aggregate_key",
        "duplicate_key",
        "merge_key",
        "replace_key",
        "replacing_key",
        "deduplicate_key",
        "key",
    }:
        return _PHYSICAL_PROPERTY_SEMANTIC

    if "partition" in key or key in {"ttl", "retention", "retention_period"}:
        return _PHYSICAL_PROPERTY_SEMANTIC

    if key in {
        "distributed_by",
        "distribution",
        "distribution_key",
        "bucket",
        "buckets",
        "bucket_count",
        "cluster",
        "cluster_by",
        "clustered_by",
        "sort_key",
        "order_by",
        "compression",
        "codec",
        "replication_num",
        "replication",
        "storage_policy",
        "bloom_filter_columns",
        "bloom_filter",
        "index",
    }:
        return _PHYSICAL_PROPERTY_LAYOUT

    return _PHYSICAL_PROPERTY_UNKNOWN


def _normalize_physical_property_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


@dataclass
class SnapshotIntervalClearRequest:
    # affected snapshot
    snapshot: SnapshotIdAndVersion

    # which interval to clear
    interval: Interval

    # which environments this snapshot is currently promoted
    # note that this can be empty if the snapshot exists because its ttl has not expired
    # but it is not part of any particular environment
    environment_names: t.Set[str] = field(default_factory=set)

    @property
    def snapshot_id(self) -> SnapshotId:
        return self.snapshot.snapshot_id

    @property
    def sorted_environment_names(self) -> t.List[str]:
        return list(sorted(self.environment_names))


def identify_restatement_intervals_across_snapshot_versions(
    state_reader: StateReader,
    prod_restatements: t.Dict[str, Interval],
    disable_restatement_models: t.Set[str],
    loaded_snapshots: t.Dict[SnapshotId, Snapshot],
    current_ts: t.Optional[int] = None,
) -> t.Dict[SnapshotId, SnapshotIntervalClearRequest]:
    """
    Given a map of snapshot names + intervals to restate in prod:
        - Look up matching snapshots (match based on name - regardless of version, to get all versions)
        - For each match, also match downstream snapshots in each dev environment while filtering out models that have restatement disabled
        - Return a list of all snapshots that are affected + the interval that needs to be cleared for each

    The goal here is to produce a list of intervals to invalidate across all dev snapshots so that a subsequent plan or
    cadence run in those environments causes the intervals to be repopulated.
    """
    if not prod_restatements:
        return {}

    # Although :loaded_snapshots is sourced from RestatementStage.all_snapshots, since the only time we ever need
    # to clear intervals across all environments is for prod, the :loaded_snapshots here are always from prod
    prod_name_versions: t.Set[SnapshotNameVersion] = {
        s.name_version for s in loaded_snapshots.values()
    }

    snapshot_intervals_to_clear: t.Dict[SnapshotId, SnapshotIntervalClearRequest] = {}

    for env_summary in state_reader.get_environments_summary():
        # Fetch the full environment object one at a time to avoid loading all environments into memory at once
        env = state_reader.get_environment(env_summary.name)
        if not env:
            logger.warning("Environment %s not found", env_summary.name)
            continue

        snapshots_by_name = {s.name: s.table_info for s in env.snapshots}

        # We dont just restate matching snapshots, we also have to restate anything downstream of them
        # so that if A gets restated in prod and dev has A <- B <- C, B and C get restated in dev
        env_dag = DAG({s.name: {p.name for p in s.parents} for s in env.snapshots})

        for restate_snapshot_name, interval in prod_restatements.items():
            if restate_snapshot_name not in snapshots_by_name:
                # snapshot is not promoted in this environment
                continue

            affected_snapshot_names = [
                x
                for x in ([restate_snapshot_name] + env_dag.downstream(restate_snapshot_name))
                if x not in disable_restatement_models
            ]

            for affected_snapshot_name in affected_snapshot_names:
                affected_snapshot = snapshots_by_name[affected_snapshot_name]

                # Don't clear intervals for a dev snapshot if it shares the same physical version with prod.
                # Otherwise, prod will be affected by what should be a dev operation
                if affected_snapshot.name_version in prod_name_versions:
                    continue

                clear_request = snapshot_intervals_to_clear.get(affected_snapshot.snapshot_id)
                if not clear_request:
                    clear_request = SnapshotIntervalClearRequest(
                        snapshot=affected_snapshot.id_and_version, interval=interval
                    )
                    snapshot_intervals_to_clear[affected_snapshot.snapshot_id] = clear_request

                clear_request.environment_names |= set([env.name])

    # snapshot_intervals_to_clear now contains the entire hierarchy of affected snapshots based
    # on building the DAG for each environment and including downstream snapshots
    # but, what if there are affected snapshots that arent part of any environment?
    unique_snapshot_names = set(snapshot_id.name for snapshot_id in snapshot_intervals_to_clear)

    current_ts = current_ts or now_timestamp()
    all_matching_non_prod_snapshots = {
        s.snapshot_id: s
        for s in state_reader.get_snapshots_by_names(
            snapshot_names=unique_snapshot_names, current_ts=current_ts, exclude_expired=True
        )
        # Don't clear intervals for a snapshot if it shares the same physical version with prod.
        # Otherwise, prod will be affected by what should be a dev operation
        if s.name_version not in prod_name_versions
    }

    # identify the ones that we havent picked up yet, which are the ones that dont exist in any environment
    if remaining_snapshot_ids := set(all_matching_non_prod_snapshots).difference(
        snapshot_intervals_to_clear
    ):
        # these snapshot id's exist in isolation and may be related to a downstream dependency of the :prod_restatements,
        # rather than directly related, so we can't simply look up the interval to clear based on :prod_restatements.
        # To figure out the interval that should be cleared, we can match to the existing list based on name
        # and conservatively take the widest interval that shows up
        snapshot_name_to_widest_interval: t.Dict[str, Interval] = {}
        for s_id, clear_request in snapshot_intervals_to_clear.items():
            current_start, current_end = snapshot_name_to_widest_interval.get(
                s_id.name, clear_request.interval
            )
            next_start, next_end = clear_request.interval

            next_start = min(current_start, next_start)
            next_end = max(current_end, next_end)

            snapshot_name_to_widest_interval[s_id.name] = (next_start, next_end)

        for remaining_snapshot_id in remaining_snapshot_ids:
            remaining_snapshot = all_matching_non_prod_snapshots[remaining_snapshot_id]
            snapshot_intervals_to_clear[remaining_snapshot_id] = SnapshotIntervalClearRequest(
                snapshot=remaining_snapshot,
                interval=snapshot_name_to_widest_interval[remaining_snapshot_id.name],
            )

    # for any affected full_history_restatement_only snapshots, we need to widen the intervals being restated to
    # include the whole time range for that snapshot. This requires a call to state to load the full snapshot record,
    # so we only do it if necessary
    full_history_restatement_snapshot_ids = [
        # FIXME: full_history_restatement_only is just one indicator that the snapshot can only be fully refreshed, the other one is Model.depends_on_self
        # however, to figure out depends_on_self, we have to render all the model queries which, alongside having to fetch full snapshots from state,
        # is problematic in secure environments that are deliberately isolated from arbitrary user code (since rendering a query may require user macros to be present)
        # So for now, these are not considered
        s_id
        for s_id, s in snapshot_intervals_to_clear.items()
        if s.snapshot.full_history_restatement_only
    ]
    if full_history_restatement_snapshot_ids:
        # only load full snapshot records that we havent already loaded
        additional_snapshots = state_reader.get_snapshots(
            [
                s.snapshot_id
                for s in full_history_restatement_snapshot_ids
                if s.snapshot_id not in loaded_snapshots
            ]
        )

        all_snapshots = loaded_snapshots | additional_snapshots

        for full_snapshot_id in full_history_restatement_snapshot_ids:
            full_snapshot = all_snapshots[full_snapshot_id]
            intervals_to_clear = snapshot_intervals_to_clear[full_snapshot_id]

            original_start, original_end = intervals_to_clear.interval

            # get_removal_interval() widens intervals if necessary
            new_interval = full_snapshot.get_removal_interval(
                start=original_start, end=original_end
            )

            intervals_to_clear.interval = new_interval

    return snapshot_intervals_to_clear
