def _optimizer_checkpoint_scalars(optimizer):
    """Bounded read-only scalar view; never invoke compatibility optimizer properties.

    Chained wrappers are traversed through their stored child lists before any
    leaf state is inspected. TE/Apex group steps are read only for nonempty
    groups, because checkpoint serialization may normalize empty-group steps.
    """
    counters = {}
    groups = {}
    unavailable = []
    queue = [('optimizer', optimizer)]
    seen = set()
    sentinel = object()

    def scalar(value, label):
        try:
            return _bounded_scalar(value)
        except Exception as exc:
            unavailable.append(label + '_unavailable_' + type(exc).__name__)
            return None

    while queue and len(seen) < 16:
        label, current = queue.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        try:
            attrs = object.__getattribute__(current, '__dict__')
        except (AttributeError, TypeError):
            unavailable.append(label + '_attributes_unavailable')
            continue
        if not isinstance(attrs, dict):
            unavailable.append(label + '_attributes_not_dict')
            continue

        # Reading ChainedOptimizer.optimizer would assert for dense+expert chains.
        # Only inspect stored attributes; do not invoke descriptors or properties.
        children = None
        child_key = None
        for key in ('chained_optimizers', 'optimizers', 'sub_optimizers'):
            value = attrs.get(key)
            if isinstance(value, (list, tuple)):
                children, child_key = value, key
                break
        if children is not None:
            queue.extend((f'{label}.{child_key}{index}', child) for index, child in enumerate(children[:8]))
            if len(children) > 8:
                unavailable.append(label + '_child_budget_exhausted')
            continue
        inner = attrs.get('optimizer', sentinel)
        if inner is not sentinel and inner is not None and inner is not current:
            queue.append((label + '.inner', inner))
            continue

        for key in ('step_count', 'num_steps', '_step_count'):
            if key in attrs and len(counters) < 32:
                value = scalar(attrs[key], label + '.' + key)
                if value is not None:
                    counters[label + '.' + key] = value
        state = attrs.get('state')
        param_groups = attrs.get('param_groups')
        if not isinstance(param_groups, list):
            unavailable.append(label + '_param_groups_unavailable')
            continue
        for group_index, group in enumerate(param_groups[:16]):
            if not isinstance(group, dict):
                unavailable.append(label + '_param_group_not_dict')
                continue
            group_label = f'{label}.group{group_index}'
            if len(groups) < 32:
                groups[group_label] = {key: scalar(group[key], group_label + '.' + key)
                                      for key in ('lr', 'betas', 'eps', 'weight_decay') if key in group}
            parameters = group.get('params', ())
            if not isinstance(parameters, (list, tuple)):
                unavailable.append(group_label + '_params_unavailable')
                continue
            if not parameters:
                continue
            # TE/Apex stores the iteration counter on the param group.
            if 'step' in group:
                if len(counters) < 32:
                    value = scalar(group['step'], group_label + '.step')
                    if value is not None:
                        counters[group_label + '.step'] = value
                continue
            # Native torch optimizers store per-parameter step counters instead.
            if isinstance(state, dict):
                for parameter_index, parameter in enumerate(parameters[:16]):
                    if len(counters) >= 32:
                        break
                    entry = state.get(parameter, {})
                    if isinstance(entry, dict) and 'step' in entry:
                        counter_label = f'{group_label}.param{parameter_index}.step'
                        value = scalar(entry['step'], counter_label)
                        if value is not None:
                            counters[counter_label] = value
    if queue:
        unavailable.append('optimizer_node_budget_exhausted')
    if not counters:
        unavailable.append('optimizer_step_counters_not_exposed_by_supported_attributes')
    if not groups:
        unavailable.append('optimizer_param_group_scalars_not_exposed')
    return {'step_counters': counters, 'param_groups': groups, 'unavailable': sorted(set(unavailable))}
