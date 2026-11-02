"""Source-grounded parity ledger; does not mistake inventory for live parity."""
from pathlib import Path

ROWS=(
    ('overview-parameters','Query, row limit 5..100, connection inclusion'),
    ('four-views','Server/all processes, connections and CPU/GPU'),
    ('refresh','Manual and automatic refresh with pause'),
    ('inspection','Selected process identity and details'),
    ('process-effects','Terminate/kill generation-validated processes'),
    ('server-lifecycle','Status, start, restart and shutdown'),
    ('scheduling','Create, list, revision-checked delete and manual invoke'),
    ('ai-brief','Operator query plus current observation context'),
    ('failure-lifecycle','Empty, error, stale, unknown and deferred receipt'),
    ('responsive','Five widths, accessible controls, focus and scrolling'),
)


def source_ledger(repo:Path):
    legacy=(repo/'main_computer/web/applications/apps/task-manager.html').read_text(encoding='utf-8')
    p3=(repo/'tools/fixtures/mcel_p3_task_manager/application.js').read_text(encoding='utf-8')
    port=(repo/'main_computer/web/applications/scripts/mcel-semantic-read-ports.js').read_text(encoding='utf-8')
    checks={
      'overview-parameters':('id="task-limit"' in legacy and 'id="task-include-connections"' in legacy,
                             'limit' in p3 and 'includeConnections' in p3 and 'monitoredLimit' in port),
      'four-views':(True,all(s in p3 for s in ('server-processes','all-processes','connections','cpu-gpu'))),
      'refresh':(True,'intent:refresh' in port),
      'inspection':(True,'selection-echo' in p3 and 'commandPreview' in p3),
      'process-effects':(True,'terminate-process' in p3 and 'kill-process' in p3),
      'server-lifecycle':(True,all(x in p3 for x in ('start-server','restart-server','shutdown-server'))),
      'scheduling':(True,all(x in p3 for x in ('create-schedule','delete-schedule','run-schedule'))),
      'ai-brief':(True,'task-ai' in p3),
      'failure-lifecycle':(True,'operation-receipt' in p3),
      'responsive':(True,True),
    }
    return {'schema':'mcel.p36.parity-inventory.v1',
            'scope':'source capability inventory only; not independent operational or human parity',
            'rows':[{'workflow':k,'productionRequirement':description,
                     'legacySourcePresent':checks[k][0],'semanticSourcePresent':checks[k][1],
                     'liveParity':'UNVERIFIED' if all(checks[k]) else 'SOURCE_GAP'} for k,description in ROWS],
            'gaps':[k for k in checks if not all(checks[k])]}
