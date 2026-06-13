# Blueprint library

Drop quickfort `.csv` / `.xlsx` blueprints here to make them available to the
steward's `dig_blueprint` tool (it resolves a blueprint by file name against
this directory). None ship by default: the Phase 2 steward governs primarily
through work orders (`set_order`) and labor assignment (`assign_labor`), and a
curated blueprint set is a level-design concern out of scope for the go/no-go
gate. If `dig_blueprint` is called and finds nothing here, it returns the empty
list as a graceful tool error rather than failing the run.
