"""Read-only CLI projection of native materialization scaffold and validation."""
from __future__ import annotations

from pathlib import Path


def command(args):
    from .calculation_review import read_bounded_json
    from .equipment.agent_execution_materialization import (
        MAX_BYTES,
        AgentExecutionMaterializationError,
        lint_agent_execution_materialization,
        new_agent_execution_materialization,
    )
    from .equipment.event_topology import (
        MAX_BYTES as TOPOLOGY_BYTES,
    )
    from .equipment.event_topology import (
        EventTopologyError,
        lint_event_topology,
        new_event_topology,
    )
    from .equipment.files import json_bytes, publish_files, read_plan
    from .equipment.schema import PlanError

    if not args.plan:
        raise PlanError('材料化作者接口需要--plan当前原生方案。', 'materialization_plan_required')
    plan = read_plan(args.plan)['plan']
    topology = plan.get('extensions', {}).get('event_topology')
    topology_mode = tuple(args._command_path) == ('equipment', 'topology')
    author_request = getattr(args, 'request', None)
    report_path = getattr(args, 'report', None)
    if author_request:
        from .authoring_assistance import authoring_assistance
        if topology_mode or args.input:
            raise PlanError('--request作者配方不能与--input完整材料化校验或topology命令混用。',
                            'authoring_request_invalid')
        request = read_bounded_json(author_request, 8 * 1024 * 1024)
        report = read_bounded_json(report_path, 128 * 1024 * 1024) if report_path else None
        if isinstance(report, dict) and report.get('code') == 0 and isinstance(report.get('data'), dict):
            report = report['data']
        data = authoring_assistance(request, plan=plan, composition_report=report)
        if args.output:
            inputs = [args.plan, author_request, Path(args.data_dir) / 'genshin.db', *([report_path] if report_path else [])]
            data['files'] = publish_files([(args.output, json_bytes(data))],
                overwrite=args.overwrite, input_paths=inputs)
        return data
    if report_path:
        raise PlanError('--report只供--request绑定恢复配方使用。', 'authoring_request_invalid')
    if not topology_mode and not isinstance(topology, dict):
        raise PlanError('先创建当前方案事件拓扑，再准备材料化输入。', 'agent_materialization_topology_required')
    try:
        if topology_mode:
            from .equipment.action_authoring_projection import (
                action_checkpoint,
                native_action_board,
            )
            checkpoint = action_checkpoint(native_action_board(plan), plan['revision'])
            document = (read_bounded_json(args.input, TOPOLOGY_BYTES) if args.input else
                new_event_topology('topology.draft', action_checkpoint=checkpoint))
            validation = lint_event_topology(document)
            declared_checkpoint = document.get('action_checkpoint') if isinstance(document, dict) else None
            checkpoint_matches = (isinstance(declared_checkpoint, dict)
                and declared_checkpoint.get('fingerprint') == checkpoint['fingerprint']
                and declared_checkpoint.get('action_ids') == checkpoint['action_ids'])
            data = {'operation': 'validate' if args.input else 'scaffold', 'document': document,
                    'validation': validation, 'current_action_checkpoint': checkpoint,
                    'action_checkpoint_matches': checkpoint_matches,
                    'context_status': 'current' if checkpoint_matches else 'stale'}
            if validation['status'] != 'invalid':
                from .equipment.event_topology_binding import (
                    new_event_topology_bindings,
                )
                data['execution_bindings_scaffold'] = new_event_topology_bindings(document)
        elif args.input:
            document = read_bounded_json(args.input, MAX_BYTES)
            data = {'operation': 'validate', 'document': document,
                    'validation': lint_agent_execution_materialization(plan, topology, document)}
        else:
            document = new_agent_execution_materialization(plan, topology, 'materialization.draft')
            data = {'operation': 'scaffold', 'document': document,
                    'validation': lint_agent_execution_materialization(plan, topology, document)}
    except (AgentExecutionMaterializationError, EventTopologyError) as exc:
        raise PlanError(str(exc), exc.code, {'path': exc.path}) from exc
    data.update(contract='event-topology-authoring-v1' if topology_mode else 'agent-materialization-authoring-v1', plan_modified=False,
                database_access=False, calculation_executed=False, game_facts_verified=False)
    if args.output:
        inputs = [args.plan, Path(args.data_dir) / 'genshin.db', *([args.input] if args.input else [])]
        data['files'] = publish_files([(args.output, json_bytes(data))],
            overwrite=args.overwrite, input_paths=inputs)
    return data
