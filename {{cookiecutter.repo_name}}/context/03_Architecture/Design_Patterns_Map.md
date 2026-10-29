# Design patterns map

Where each pattern from the design-patterns reading list is used, so a new contributor extends the code the same way.

| Pattern | Where | Why |
|---|---|---|
| Repository | `metastore.repository.MetadataRepository` (Protocol) | publishers and collectors never know which database they write to |
| Adapter | `SqlAlchemyRepository` (PostgreSQL, Lakebase, SQLite), `UcRepository`; `SdkWorkspaceGateway` over the SDK; `metadata.export_ontos` over Ontos's shapes | one internal model, many external shapes |
| Strategy | `CredentialProvider` (secret scope, Lakebase OAuth, environment); `SqlExecutor`; promotion gate policies (`MetricThreshold`, `EvalPassRate`) | behaviour chosen by configuration |
| Factory Method | `RepositoryFactory.from_config` | builds the sinks a target configures |
| Composite | `CompositeRepository` | publish to several sinks as one |
| Proxy | `WriterGuard` | enforces who may write which rows, in front of any repository |
| Facade | `MetadataPublisher`, `lifecycle` entry points | one call for a multi-step operation |
| Template Method | `reconcile.Reconciler` (read actual, diff, plan, apply) and `BaseRepository` | five reconcilers and two repositories share one algorithm |
| Chain of Responsibility (rule chain) | `metadata.rules.DEFAULT_RULES` | independent rules, one run reports every problem |
| Dependency inversion | gateways and clients are injected; tests use fakes | nothing here needs a workspace to test |
| SCD type 2 | definition tables, `record_version`, `valid_from`/`valid_to` | full history of every definition |
| Chain of Responsibility (advisor) | `metastore.tuning.DEFAULT_TUNING_RULES`; `metadata.ingestion.recommend` | each rule reports one recommendation independently |
| Strategy (attribution) | `metastore.cost.attribute`: tags, then deployment record, then job-name prefix | the most reliable evidence wins, and says which it was |
| Adapter | `metastore.cost.SparkBillingReader`, `insights.SqlExecutor` | system tables and SQL warehouses behind small Protocols |

Rules of thumb: add a tuning rule as a function in `tuning.py` and `DEFAULT_TUNING_RULES`; add an owner view as a
numbered file in `insights/sql/` (it may read only system tables, `platform_meta` objects and earlier views — a test
checks); add a rule as a new function in `rules.py` and `DEFAULT_RULES`; add a database as a new `SqlExecutor` or
dialect strategy; add a reconciler as a `Reconciler` subclass with a desired-state dataclass.
