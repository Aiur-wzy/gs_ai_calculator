"""Finite periodic boundary qualification, derived from bound adapter evidence."""
from .periodic_lifecycle import project_periodic_lifecycle


def periodic_execution_scope(runtime, effective=None, actual_rotation=None):
    live = []
    effective_entries = {e['entry_id']: e for e in (effective or {}).get('entries', [])}
    for entry in (runtime or {}).get('entries', []):
        source = effective_entries.get(entry['entry_id'])
        periodic = entry.get('family') == 'periodic_channel' or (source or {}).get('family') == 'periodic_channel'
        projections = [item for item in entry.get('installed', [])
            if str(item.get('target_identity', '')).startswith('periodic_lifecycle:')]
        if periodic and len(projections) != 1:
            raise ValueError('Periodic entry lacks its complete lifecycle projection evidence')
        for installed in projections:
            boundary = installed.get('boundary')
            if boundary is None:
                raise ValueError('Periodic lifecycle projection lacks its boundary evidence')
            inputs = installed.get('projection_inputs')
            if not isinstance(inputs, dict):
                raise TypeError('Periodic boundary lacks its bounded projection inputs')
            if effective is not None:
                from .agent_execution_runtime import _periodic_dispatch_steps
                from .files import fingerprint
                topology = installed.get('projection_topology')
                if not source or fingerprint(topology) != effective['topology_fingerprint']:
                    raise ValueError('Periodic boundary topology is not bound to its effective input')
                if actual_rotation is not None and any(installed.get('projection_rotation', {}).get(key)
                        != actual_rotation.get(key) for key in ('period', 'actions')):
                    raise ValueError('Periodic projection is not bound to the actual executed action window')
                _, independently_installed = _periodic_dispatch_steps(source, topology, installed.get('projection_rotation'))
                if independently_installed != installed:
                    raise ValueError('Periodic boundary does not match the actual source adapter projection')
            rebuilt = project_periodic_lifecycle(**inputs)
            if rebuilt.get('boundary') != boundary or rebuilt['generations'] != installed.get('generations'):
                raise ValueError('Periodic boundary cannot be independently reconstructed')
            if boundary['status'] in {'active', 'paused'}:
                live.append({'entry_id': entry['entry_id'], 'boundary': boundary})
    return {'contract': 'periodic-execution-scope-v1',
        'scope': 'finite_input_window' if live else 'no_live_periodic_boundary',
        'steady_state_proven': False if live else None, 'live_boundaries': live}


def periodic_steady_state_allowed(result):
    runtime = result.get('agent_execution_runtime_receipt')
    # Read-only consumers derive from the same actual receipt. Saved readers
    # separately validate the materialization/effective/runtime proof chain.
    return periodic_execution_scope(runtime)['steady_state_proven'] is not False
