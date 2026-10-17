"""Select a bounded-DOM mesh renderer without altering scientific artifacts."""
INTERACTIVE_CELL_LIMIT = 2000
BATCHED_CELL_LIMIT = 100000


def display_policy(count):
    if count > BATCHED_CELL_LIMIT:
        mode = 'summary'
    elif count > INTERACTIVE_CELL_LIMIT:
        mode = 'batched'
    else:
        mode = 'interactive'
    return dict(mode=mode, cell_count=count,
                interactive_cell_limit=INTERACTIVE_CELL_LIMIT,
                batched_cell_limit=BATCHED_CELL_LIMIT)


def artifact_view(name, data):
    """Return a UI view, never modify the retained complete artifact."""
    result = dict(data)
    if name in {'cells', 'attributes'}:
        rows = data['cells']
        result['display'] = display_policy(len(rows))
        if name == 'attributes':
            result['summary'] = dict(cell_count=len(rows),
                valid_count=sum(row['value'] is not None for row in rows),
                missing_count=sum(row['value'] is None for row in rows),
                full_count=sum(row['footprint_coverage'] >= 1-1e-8 and
                               row['valid_coverage'] >= 1-1e-8 for row in rows))
        if result['display']['mode'] == 'summary':
            result['cells'] = []
    elif name == 'qa' and 'mesh_quality' in data:
        # Per-cell centres remain in the downloadable QA, not the summary panel.
        result['mesh_quality'] = {key:value for key,value in data['mesh_quality'].items() if key != 'centres'}
        if isinstance(data.get('historical_qa'),dict):
            history=dict(data['historical_qa'])
            if isinstance(history.get('mesh_quality'),dict):
                history['mesh_quality']={key:value for key,value in history['mesh_quality'].items() if key!='centres'}
            result['historical_qa']=history
    return result
