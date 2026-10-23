"""Generate a distinct read-only authenticated completion inspection wrapper."""
from pathlib import Path
import json
B=Path(__file__).resolve().parent
source=(B/'prepare-readonly-io-wrapper.py').read_text()
names=['verify_completed_production_migration.py','execute_active_host_migration.py']+list(json.loads((B/'production-migration-execution-contract.private.json').read_text())['source_pins'])
start=source.index('names=');end=source.index('\npins=',start)
source=source[:start]+'names='+repr(names)+source[end:]
source=source.replace('inspect_production_migration_io.py','verify_completed_production_migration.py').replace('migration-io-','migration-completed-').replace('Migration-IO-','Migration-Completed-')
source=source.replace('$inspection.production_activation_available -ne $false','$inspection.mutation_apis_denied -ne $true').replace('physical_io_readonly_passed','authenticated_completion_readonly_passed')
# Executing only the generator creates separate scripts; it invokes no task.
exec(compile(source,str(B/'prepare-readonly-completion-wrapper.py'),'exec'),{'__file__':str(__file__),'__name__':'__main__'})
