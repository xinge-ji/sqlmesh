from __future__ import annotations

import json
import logging
import os
import typing as t
from ast import literal_eval
from dataclasses import asdict

import agate
import jinja2
from dbt import version
from dbt.adapters.base import BaseRelation, Column
from ruamel.yaml import YAMLError
from sqlglot import Dialect

from sqlmesh.core.engine_adapter import EngineAdapter
from sqlmesh.core.snapshot.definition import DeployabilityIndex
from sqlmesh.dbt.adapter import BaseAdapter, ParsetimeAdapter, RuntimeAdapter
from sqlmesh.dbt.relation import Policy
from sqlmesh.dbt.target import TARGET_TYPE_TO_CONFIG_CLASS
from sqlmesh.dbt.util import DBT_VERSION
from sqlmesh.utils import AttributeDict, yaml
from sqlmesh.utils.date import now
from sqlmesh.utils.errors import ConfigError, MacroEvalError
from sqlmesh.utils.jinja import JinjaMacroRegistry, MacroReference, MacroReturnVal

logger = logging.getLogger(__name__)


class Exceptions:
    def raise_compiler_error(self, msg: str) -> None:
        if DBT_VERSION >= (1, 4, 0):
            from dbt.exceptions import CompilationError

            raise CompilationError(msg)
        else:
            from dbt.exceptions import CompilationException  # type: ignore

            raise CompilationException(msg)

    def raise_not_implemented(self, msg: str) -> None:
        raise NotImplementedError(msg)

    def warn(self, msg: str) -> str:
        logger.warning(msg)
        return ""


class Api:
    def __init__(self, dialect: t.Optional[str]) -> None:
        if dialect:
            config_class = TARGET_TYPE_TO_CONFIG_CLASS[
                Dialect.get_or_raise(dialect).__class__.__name__.lower()
            ]
            self.Relation = config_class.relation_class
            self.Column = config_class.column_class
            self.quote_policy = config_class.quote_policy
        else:
            self.Relation = BaseRelation
            self.Column = Column
            self.quote_policy = Policy()


class Flags:
    def __init__(
        self,
        full_refresh: t.Optional[str] = None,
        store_failures: t.Optional[str] = None,
        which: t.Optional[str] = None,
    ) -> None:
        # Temporary placeholder values for now (these are generally passed from the CLI)
        self.FULL_REFRESH = full_refresh
        self.STORE_FAILURES = store_failures
        self.WHICH = which


class Modules:
    def __init__(self) -> None:
        import datetime
        import itertools
        import re

        try:
            import pytz

            self.pytz = pytz
        except ImportError:
            pass

        self.datetime = datetime
        self.re = re
        self.itertools = itertools


class SQLExecution:
    def __init__(self, adapter: BaseAdapter):
        self.adapter = adapter
        self._results: t.Dict[str, AttributeDict] = {}

    def store_result(self, name: str, response: t.Any, agate_table: t.Optional[agate.Table]) -> str:
        from sqlmesh.dbt.util import empty_table, as_matrix

        if agate_table is None:
            agate_table = empty_table()

        self._results[name] = AttributeDict(
            {
                "response": response,
                "data": as_matrix(agate_table),
                "table": agate_table,
            }
        )
        return ""

    def load_result(self, name: str) -> t.Optional[AttributeDict]:
        return self._results.get(name)

    def run_query(self, sql: str) -> agate.Table:
        self.statement("run_query_statement", fetch_result=True, auto_begin=False, caller=sql)
        resp = self.load_result("run_query_statement")
        assert resp is not None
        return resp["table"]

    def statement(
        self,
        name: t.Optional[str],
        fetch_result: bool = False,
        auto_begin: bool = True,
        language: str = "sql",
        caller: t.Optional[jinja2.runtime.Macro | str] = None,
    ) -> str:
        """
        Executes the SQL that is defined within the context of the caller. Therefore caller really isn't optional
        but we make it optional and at the end because we need to match the signature of the jinja2 macro.

        Name is the name that we store the results to which can be retrieved with `load_result`. If name is not
        provided then the SQL is executed but the results are not stored.
        """
        if not caller:
            raise RuntimeError(
                "Statement relies on a caller to be set that is the target SQL to be run"
            )
        sql = caller if isinstance(caller, str) else caller()
        if language != "sql":
            raise NotImplementedError(
                "SQLMesh's dbt integration only supports SQL statements at this time."
            )
        res, table = self.adapter.execute(sql, fetch=fetch_result, auto_begin=auto_begin)
        if name:
            self.store_result(name, res, table)
        return ""


class Var:
    def __init__(self, variables: t.Dict[str, t.Any]) -> None:
        self.variables = variables

    def __call__(self, name: str, default: t.Optional[t.Any] = None, **kwargs: t.Any) -> t.Any:
        return self.variables.get(name, default)

    def has_var(self, name: str) -> bool:
        return name in self.variables


def env_var(name: str, default: t.Optional[str] = None) -> t.Optional[str]:
    if name not in os.environ and default is None:
        raise ConfigError(f"Missing environment variable '{name}'")
    return os.environ.get(name, default)


def log(msg: str, info: bool = False) -> str:
    logger.debug(msg)
    return ""


def generate_ref(refs: t.Dict[str, t.Any], api: Api) -> t.Callable:
    def ref(
        package: str, name: t.Optional[str] = None, **kwargs: t.Any
    ) -> t.Optional[BaseRelation]:
        version = kwargs.get("version", kwargs.get("v"))
        ref_name = f"{package}.{name}" if name else package

        if version is not None:
            relation_info = refs.get(f"{ref_name}_v{version}")
            if relation_info is None:
                logger.warning(
                    "Could not resolve ref '%s' with version '%s'. Falling back to unversioned reference",
                    ref_name,
                    version,
                )
                relation_info = refs.get(ref_name)
        else:
            relation_info = refs.get(ref_name)
            if not relation_info:
                versioned_infos = sorted(
                    [(r, info) for r, info in refs.items() if r.startswith(f"{ref_name}_v")],
                    key=lambda i: i[0],
                )
                if versioned_infos:
                    relation_info = versioned_infos[-1][1]

        if relation_info is None:
            logger.debug("Could not resolve ref '%s', version '%s'", ref_name, version)
            return None

        return _relation_info_to_relation(relation_info, api.Relation, api.quote_policy)

    return ref


def generate_source(sources: t.Dict[str, t.Any], api: Api) -> t.Callable:
    def source(package: str, name: str) -> t.Optional[BaseRelation]:
        relation_info = sources.get(f"{package}.{name}")
        if relation_info is None:
            logger.debug("Could not resolve source package='%s' name='%s'", package, name)
            return None

        # Clickhouse uses a 2-level schema.table naming scheme, where the second level is called
        # a "database" (instead of "schema" as one would reasonably assume). This can lead to confusion
        # because it is not clear how Clickhouse identifiers map onto dbt's "database" and "schema" fields.
        #
        # This confusion can occur in source resolution. If a source's `schema` is not explicitly specified,
        # the source name is used as the schema by default.
        #
        # If a source specified the `database` field and the schema has defaulted to the source name,
        # we follow dbt-clickhouse in assuming that the user intended for the `database` field to be the
        # second level identifier.
        # https://github.com/ClickHouse/dbt-clickhouse/blob/065f3a724fa09205446ecadac7a00d92b2d8c646/dbt/adapters/clickhouse/relation.py#L112
        #
        # NOTE: determining relation class based on name so we don't introduce a dependency on dbt-clickhouse
        if (
            api.Relation.__name__ == "ClickHouseRelation"
            and relation_info.schema == package
            and relation_info.database
        ):
            relation_info["schema"] = relation_info["database"]
            relation_info["database"] = ""

        return _relation_info_to_relation(relation_info, api.Relation, api.quote_policy)

    return source


def return_val(val: t.Any) -> None:
    raise MacroReturnVal(val)


def to_set(value: t.Any, default: t.Optional[t.Any] = None) -> t.Optional[t.Any]:
    try:
        return set(value)
    except TypeError:
        return default


def to_json(value: t.Any, default: t.Optional[t.Any] = None) -> t.Optional[t.Any]:
    try:
        return json.dumps(value)
    except TypeError:
        return default


def from_json(value: str, default: t.Optional[t.Any] = None) -> t.Optional[t.Any]:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def to_yaml(value: t.Any, default: t.Optional[t.Any] = None) -> t.Optional[t.Any]:
    try:
        return yaml.dump(value)
    except (TypeError, YAMLError):
        return default


def from_yaml(value: t.Any, default: t.Optional[t.Any] = None) -> t.Optional[t.Any]:
    try:
        return dict(yaml.load(value, raise_if_empty=False, render_jinja=False))
    except (TypeError, YAMLError):
        return default


def do_zip(*args: t.Any, default: t.Optional[t.Any] = None) -> t.Optional[t.Any]:
    try:
        return list(zip(*args))
    except TypeError:
        return default


def as_bool(value: str) -> bool:
    result = _try_literal_eval(value)
    if isinstance(result, bool):
        return result
    raise MacroEvalError(f"Failed to convert '{value}' into boolean.")


def as_number(value: str) -> t.Any:
    result = _try_literal_eval(value)
    if isinstance(value, (int, float)) and not isinstance(result, bool):
        return result
    raise MacroEvalError(f"Failed to convert '{value}' into number.")


def _try_literal_eval(value: str) -> t.Any:
    try:
        return literal_eval(value)
    except (ValueError, SyntaxError, MemoryError):
        return value


BUILTIN_GLOBALS = {
    "dbt_version": version.__version__,
    "env_var": env_var,
    "exceptions": Exceptions(),
    "fromjson": from_json,
    "fromyaml": from_yaml,
    "log": log,
    "modules": Modules(),
    "print": log,
    "return": return_val,
    "set": to_set,
    "set_strict": set,
    "sqlmesh": True,
    "sqlmesh_incremental": True,
    "tojson": to_json,
    "toyaml": to_yaml,
    "zip": do_zip,
    "zip_strict": lambda *args: list(zip(*args)),
}

BUILTIN_FILTERS = {
    "as_bool": as_bool,
    "as_native": _try_literal_eval,
    "as_number": as_number,
    "as_text": lambda v: "" if v is None else str(v),
}

OVERRIDDEN_MACROS = {
    MacroReference(package="dbt", name="is_incremental"),
    MacroReference(name="is_incremental"),
}


def create_builtin_globals(
    jinja_macros: JinjaMacroRegistry,
    jinja_globals: t.Dict[str, t.Any],
    engine_adapter: t.Optional[EngineAdapter],
) -> t.Dict[str, t.Any]:
    builtin_globals = BUILTIN_GLOBALS.copy()
    jinja_globals = jinja_globals.copy()

    target: t.Optional[AttributeDict] = jinja_globals.get("target", None)
    project_dialect = jinja_globals.pop("dialect", None) or (target.get("type") if target else None)
    api = Api(project_dialect)

    builtin_globals["api"] = api

    this = jinja_globals.pop("this", None)
    if this is not None:
        if not isinstance(this, api.Relation):
            builtin_globals["this"] = api.Relation.create(**this)
        else:
            builtin_globals["this"] = this

    sources = jinja_globals.pop("sources", None)
    if sources is not None:
        builtin_globals["source"] = generate_source(sources, api)

    refs = jinja_globals.pop("refs", None)
    if refs is not None:
        builtin_globals["ref"] = generate_ref(refs, api)

    variables = jinja_globals.pop("vars", None)
    if variables is not None:
        builtin_globals["var"] = Var(variables)

    deployability_index = (
        jinja_globals.get("deployability_index") or DeployabilityIndex.all_deployable()
    )
    snapshot = jinja_globals.pop("snapshot", None)

    if snapshot and snapshot.is_incremental:
        intervals = (
            snapshot.intervals
            if deployability_index.is_deployable(snapshot)
            else snapshot.dev_intervals
        )
        is_incremental = bool(intervals)
    else:
        is_incremental = False
    builtin_globals["is_incremental"] = lambda: is_incremental

    builtin_globals["builtins"] = AttributeDict(
        {k: builtin_globals.get(k) for k in ("ref", "source", "config", "var")}
    )

    if engine_adapter is not None:
        builtin_globals["flags"] = Flags(which="run")
        adapter: BaseAdapter = RuntimeAdapter(
            engine_adapter,
            jinja_macros,
            jinja_globals={
                **builtin_globals,
                **jinja_globals,
                "engine_adapter": engine_adapter,
            },
            relation_type=api.Relation,
            column_type=api.Column,
            quote_policy=api.quote_policy,
            snapshots=jinja_globals.get("snapshots", {}),
            table_mapping=jinja_globals.get("table_mapping", {}),
            deployability_index=deployability_index,
            project_dialect=project_dialect,
        )
    else:
        builtin_globals["flags"] = Flags(which="parse")
        adapter = ParsetimeAdapter(
            jinja_macros,
            jinja_globals={**builtin_globals, **jinja_globals},
            project_dialect=project_dialect,
            quote_policy=api.quote_policy,
        )

    sql_execution = SQLExecution(adapter)
    builtin_globals.update(
        {
            "adapter": adapter,
            "execute": True,
            "load_relation": adapter.load_relation,
            "store_result": sql_execution.store_result,
            "load_result": sql_execution.load_result,
            "run_query": sql_execution.run_query,
            "statement": sql_execution.statement,
        }
    )

    builtin_globals["run_started_at"] = jinja_globals.get("execution_dt") or now()
    builtin_globals["dbt"] = AttributeDict(builtin_globals)

    return {**builtin_globals, **jinja_globals}


def create_builtin_filters() -> t.Dict[str, t.Callable]:
    return BUILTIN_FILTERS


def _relation_info_to_relation(
    relation_info: t.Dict[str, t.Any],
    relation_type: t.Type[BaseRelation],
    target_quote_policy: Policy,
) -> BaseRelation:
    relation_info = relation_info.copy()
    quote_policy = Policy(
        **{
            **asdict(target_quote_policy),
            **{k: v for k, v in relation_info.pop("quote_policy", {}).items() if v is not None},
        }
    )
    return relation_type.create(**relation_info, quote_policy=quote_policy)
